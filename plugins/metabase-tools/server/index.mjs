#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const BASE_URL = (process.env.METABASE_URL || "").replace(/\/+$/, "");
const API_KEY = process.env.METABASE_API_KEY;
if (!BASE_URL || !API_KEY) {
  console.error("METABASE_URL and METABASE_API_KEY must be set");
  process.exit(1);
}

async function mb(path, { method = "GET", body } = {}) {
  const res = await fetch(`${BASE_URL}/api${path}`, {
    method,
    headers: { "x-api-key": API_KEY, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!res.ok) {
    const msg = typeof data === "object" ? data.message || JSON.stringify(data) : data;
    throw new Error(`Metabase ${method} ${path} -> ${res.status}: ${String(msg).slice(0, 2000)}`);
  }
  return data;
}

const ok = (data) => ({ content: [{ type: "text", text: typeof data === "string" ? data : JSON.stringify(data, null, 1) }] });
const fail = (e) => ({ isError: true, content: [{ type: "text", text: String(e.message || e) }] });
const tool = (fn) => async (args) => { try { return ok(await fn(args)); } catch (e) { return fail(e); } };

// Compact a query result: columns + rows, truncated to `limit`.
function formatResult(r, limit) {
  if (r.status === "failed" || r.error) throw new Error(`Query failed: ${r.error || JSON.stringify(r).slice(0, 2000)}`);
  const cols = r.data.cols.map((c) => c.display_name && c.display_name !== c.name ? `${c.name} (${c.display_name})` : c.name);
  const rows = r.data.rows;
  return {
    columns: cols,
    rows: rows.slice(0, limit),
    row_count: r.row_count ?? rows.length,
    truncated: rows.length > limit || !!r.data.rows_truncated,
    running_time_ms: r.running_time,
  };
}

const server = new McpServer({ name: "metabase", version: "1.0.0" });

server.tool("list_databases", "List databases connected to Metabase (id, name, engine).", {},
  tool(async () => {
    const d = await mb("/database");
    return (d.data || d).map(({ id, name, engine }) => ({ id, name, engine }));
  }));

server.tool("list_schemas", "List schemas in a database.", { database_id: z.number() },
  tool(({ database_id }) => mb(`/database/${database_id}/schemas`)));

server.tool("list_tables", "List tables in a database, optionally restricted to one schema.",
  { database_id: z.number(), schema: z.string().optional() },
  tool(async ({ database_id, schema }) => {
    const tables = schema !== undefined
      ? await mb(`/database/${database_id}/schema/${encodeURIComponent(schema)}`)
      : (await mb(`/database/${database_id}?include=tables`)).tables;
    return tables.map(({ id, schema, name, display_name, description }) => ({ id, schema, name, display_name, description }));
  }));

server.tool("get_table_metadata", "Get a table's columns (name, type, description, FK targets).", { table_id: z.number() },
  tool(async ({ table_id }) => {
    const t = await mb(`/table/${table_id}/query_metadata`);
    return {
      id: t.id, db_id: t.db_id, schema: t.schema, name: t.name, description: t.description,
      fields: t.fields.map((f) => ({
        id: f.id, name: f.name, type: f.database_type, semantic_type: f.semantic_type,
        description: f.description, fk_target_field_id: f.fk_target_field_id,
      })),
    };
  }));

server.tool("run_sql", "Run a native SQL query against a database and return rows.",
  {
    database_id: z.number().describe("Metabase database id (see list_databases)"),
    query: z.string(),
    limit: z.number().default(200).describe("Max rows returned to the model (Metabase caps at 2000)"),
  },
  tool(async ({ database_id, query, limit }) => {
    const r = await mb("/dataset", { method: "POST", body: { database: database_id, type: "native", native: { query } } });
    return formatResult(r, limit);
  }));

server.tool("search", "Search Metabase for cards (questions), dashboards, collections, tables, models, metrics.",
  {
    query: z.string(),
    models: z.array(z.enum(["card", "dashboard", "collection", "table", "dataset", "metric", "database"])).optional(),
    limit: z.number().default(25),
  },
  tool(async ({ query, models, limit }) => {
    const p = new URLSearchParams({ q: query });
    (models || []).forEach((m) => p.append("models", m));
    const r = await mb(`/search?${p}`);
    return (r.data || r).slice(0, limit).map((x) => ({
      model: x.model, id: x.id, name: x.name, description: x.description,
      collection: x.collection?.name, database_id: x.database_id,
    }));
  }));

server.tool("get_card", "Get a saved question/model: its query (SQL or MBQL), parameters and metadata.", { card_id: z.number() },
  tool(async ({ card_id }) => {
    const c = await mb(`/card/${card_id}`);
    return {
      id: c.id, name: c.name, description: c.description, type: c.type, display: c.display,
      database_id: c.database_id, collection: c.collection?.name,
      query_type: c.dataset_query?.type,
      sql: c.dataset_query?.native?.query ?? c.dataset_query?.stages?.[0]?.native,
      dataset_query: c.dataset_query, parameters: c.parameters,
    };
  }));

server.tool("run_card", "Execute a saved question and return rows. Parameters use Metabase's parameter format.",
  {
    card_id: z.number(),
    parameters: z.array(z.record(z.any())).optional().describe('e.g. [{"type":"category","target":["variable",["template-tag","x"]],"value":"foo"}]'),
    limit: z.number().default(200),
  },
  tool(async ({ card_id, parameters, limit }) => {
    const r = await mb(`/card/${card_id}/query`, { method: "POST", body: { parameters: parameters || [] } });
    return formatResult(r, limit);
  }));

server.tool("list_collections", "List all collections.", {},
  tool(async () => (await mb("/collection")).map(({ id, name, location, archived }) => ({ id, name, location, archived }))));

server.tool("get_collection_items", "List items (cards, dashboards, sub-collections) in a collection. Use 'root' for the top level.",
  { collection_id: z.union([z.number(), z.literal("root")]) },
  tool(async ({ collection_id }) => {
    const r = await mb(`/collection/${collection_id}/items`);
    return (r.data || r).map(({ model, id, name, description }) => ({ model, id, name, description }));
  }));

server.tool("get_dashboard", "Get a dashboard with its cards and filters.", { dashboard_id: z.number() },
  tool(async ({ dashboard_id }) => {
    const d = await mb(`/dashboard/${dashboard_id}`);
    return {
      id: d.id, name: d.name, description: d.description, collection_id: d.collection_id,
      parameters: d.parameters?.map(({ id, name, slug, type }) => ({ id, name, slug, type })),
      tabs: d.tabs?.map(({ id, name }) => ({ id, name })),
      cards: d.dashcards?.map((dc) => ({
        dashcard_id: dc.id, card_id: dc.card_id, name: dc.card?.name, display: dc.card?.display,
        tab_id: dc.dashboard_tab_id, parameter_mappings: dc.parameter_mappings,
      })),
    };
  }));

server.tool("create_card", "Create a saved native SQL question.",
  {
    name: z.string(),
    database_id: z.number(),
    query: z.string(),
    collection_id: z.number().optional(),
    description: z.string().optional(),
    display: z.string().default("table"),
  },
  tool(async ({ name, database_id, query, collection_id, description, display }) => {
    const c = await mb("/card", {
      method: "POST",
      body: {
        name, description, display, collection_id: collection_id ?? null, visualization_settings: {},
        dataset_query: { database: database_id, type: "native", native: { query } },
      },
    });
    return { id: c.id, name: c.name, url: `${BASE_URL}/question/${c.id}` };
  }));

server.tool("update_card_sql", "Replace the SQL of an existing native question (optionally rename it).",
  { card_id: z.number(), query: z.string(), name: z.string().optional() },
  tool(async ({ card_id, query, name }) => {
    const c = await mb(`/card/${card_id}`);
    if (c.dataset_query?.type !== "native") throw new Error("Card is not a native SQL question");
    const dataset_query = { ...c.dataset_query, native: { ...c.dataset_query.native, query } };
    const u = await mb(`/card/${card_id}`, { method: "PUT", body: { dataset_query, ...(name ? { name } : {}) } });
    return { id: u.id, name: u.name, url: `${BASE_URL}/question/${u.id}` };
  }));

await server.connect(new StdioServerTransport());
