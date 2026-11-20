// JSON Schema (stdin) -> TypeScript (stdout). Driven by scripts/gen_types.py; do not run by hand.
import { compile } from "json-schema-to-typescript";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const schema = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const ts = await compile(schema, schema.title, {
  bannerComment:
    "/* Generated from the server's pydantic models by scripts/gen_types.py. Do not edit; regenerate. */",
  unreachableDefinitions: true,
  additionalProperties: false,
  strictIndexSignatures: true,
  enableConstEnums: false,
  style: { singleQuote: false, semi: true, printWidth: 120, endOfLine: "lf" },
});
process.stdout.write(ts);
