import { SealedOmpArchitect } from "../../src/omp";

const root = process.argv[2];
if (!root) throw new Error("expected a temporary Story Host root");
if (!process.env.OPENROUTER_API_KEY) process.env.OPENROUTER_API_KEY = "test-only-key";

const architect = new SealedOmpArchitect(root);
const names = await architect.inspectToolNames();
process.stdout.write(`${JSON.stringify(names)}\n`);
