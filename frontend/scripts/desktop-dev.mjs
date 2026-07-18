import { existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { execSync, spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(scriptDir, "..");
const workspaceRoot = resolve(frontendRoot, "..");
const windows = process.platform === "win32";

function fail(message) {
  throw new Error(`Loom desktop development preflight failed: ${message}`);
}

function executableFromEnvironment() {
  const configured = process.env.LOOM_PYTHON;
  if (configured) {
    const candidate = resolve(process.cwd(), configured);
    if (!isFile(candidate)) {
      fail(`LOOM_PYTHON does not point to an executable: ${candidate}`);
    }
    return candidate;
  }

  const candidates = windows
    ? [join(workspaceRoot, ".venv", "Scripts", "python.exe")]
    : [join(workspaceRoot, ".venv", "bin", "python")];
  const python = candidates.find(isFile);
  if (!python) {
    fail(`the repository virtual environment is missing. Run \`uv sync\` from ${workspaceRoot}.`);
  }
  return python;
}

function isFile(path) {
  try {
    return existsSync(path) && statSync(path).isFile();
  } catch {
    return false;
  }
}

function command(name) {
  return windows ? `${name}.cmd` : name;
}

function prependPath(environment, directory) {
  const separator = windows ? ";" : ":";
  const inherited = environment.PATH ?? environment.Path ?? "";
  const path = [directory, ...inherited.split(separator).filter(Boolean)]
    .filter((entry, index, all) => all.indexOf(entry) === index)
    .join(separator);
  return { ...environment, PATH: path, Path: path };
}

function cargoBinDirectory() {
  if (!windows) return undefined;
  const home = process.env.CARGO_HOME
    ? resolve(process.cwd(), process.env.CARGO_HOME)
    : join(process.env.USERPROFILE ?? process.env.HOME ?? "", ".cargo");
  const directory = join(home, "bin");
  return isFile(join(directory, "cargo.exe")) ? directory : undefined;
}

function checkCargo(environment) {
  const result = spawnSync(windows ? "cargo.exe" : "cargo", ["--version"], {
    cwd: frontendRoot,
    env: environment,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    fail("Rust/Cargo is unavailable. Install Rust with rustup, then reopen the terminal.");
  }
}

function visualStudioDevCommand() {
  if (!windows) return undefined;
  const candidates = [
    process.env.VSDEVCMD,
    ...["BuildTools", "Community", "Professional", "Enterprise"].map((edition) => join(
      process.env["ProgramFiles(x86)"] ?? "C:\\Program Files (x86)",
      "Microsoft Visual Studio",
      "2022",
      edition,
      "Common7",
      "Tools",
      "VsDevCmd.bat",
    )),
  ].filter(Boolean);
  return candidates.find(isFile);
}

function checkPython(python) {
  const result = spawnSync(python, ["-c", "import loom.lean.stdio"], {
    cwd: workspaceRoot,
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error) fail(`could not launch ${python}: ${result.error.message}`);
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || "unknown import error").trim();
    fail(`the selected Python runtime cannot import Loom${detail ? `: ${detail}` : ""}`);
  }
}

function checkTauri() {
  const binary = join(frontendRoot, "node_modules", ".bin", windows ? "tauri.cmd" : "tauri");
  if (!isFile(binary)) {
    fail(`the local Tauri CLI is missing. Run \`npm ci\` in ${frontendRoot}.`);
  }
  return binary;
}

function run(commandPath, args, environment) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(commandPath, args, {
      cwd: frontendRoot,
      env: environment,
      stdio: "inherit",
      // npm and Tauri are local Windows .cmd shims. Node cannot execute those
      // directly in every Windows environment, so route only those fixed,
      // repository-controlled commands through cmd.exe.
      shell: windows && /\.(?:cmd|bat)$/i.test(commandPath),
    });
    child.on("error", rejectRun);
    child.on("close", (code) => {
      if (code === 0) resolveRun();
      else rejectRun(new Error(`${commandPath} ${args.join(" ")} exited with ${code ?? "an unknown status"}`));
    });
  });
}

function runTauri(tauri, environment, developerCommand) {
  if (!windows) return run(tauri, ["dev"], environment);
  const commandLine = `call "${developerCommand.replaceAll('"', '""')}" -arch=x64 -host_arch=x64 >nul && set`;
  let output;
  try {
    output = execSync(commandLine, {
      cwd: frontendRoot,
      env: environment,
      encoding: "utf8",
      shell: process.env.ComSpec ?? process.env.COMSPEC ?? "cmd.exe",
      windowsHide: true,
    });
  } catch {
    fail("could not initialize the Visual Studio MSVC development environment.");
  }
  const msvcEnvironment = { ...environment };
  for (const line of output.split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    msvcEnvironment[line.slice(0, separator)] = line.slice(separator + 1);
  }
  return run(tauri, ["dev"], msvcEnvironment);
}

const python = executableFromEnvironment();
const storyRoot = process.env.LOOM_ROOT ? resolve(process.cwd(), process.env.LOOM_ROOT) : workspaceRoot;
if (!existsSync(storyRoot)) fail(`LOOM_ROOT does not exist: ${storyRoot}`);
checkPython(python);
const tauri = checkTauri();
let environment = {
  ...process.env,
  LOOM_ROOT: storyRoot,
  LOOM_PYTHON: python,
};
const cargoBin = cargoBinDirectory();
if (cargoBin) environment = prependPath(environment, cargoBin);
checkCargo(environment);
const developerCommand = windows ? visualStudioDevCommand() : undefined;
if (windows && !developerCommand) {
  fail("Visual Studio 2022 Build Tools with the C++ workload are required for the MSVC Tauri target.");
}

if (process.argv.includes("--check")) {
  console.log(JSON.stringify({
    ok: true,
    root: storyRoot,
    python,
    mode: "desktop-development",
    storyListBackend: "python",
  }));
  process.exit(0);
}

console.log(`Using repository Story runtime: ${python}`);
console.log(`Using Story data root: ${storyRoot}`);
await runTauri(tauri, environment, developerCommand);
