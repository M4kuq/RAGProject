import { spawnSync } from "node:child_process";

function npmInvocation(args) {
  if (process.env.npm_execpath) {
    return {
      command: process.execPath,
      args: [process.env.npm_execpath, ...args]
    };
  }
  return {
    command: process.platform === "win32" ? "npm.cmd" : "npm",
    args
  };
}

function countProblems(node, visited = new Set()) {
  if (!node || typeof node !== "object" || visited.has(node)) {
    return 0;
  }
  visited.add(node);
  let count = 0;
  if (node.invalid || node.extraneous || node.missing || node.peerMissing) {
    count += 1;
  }
  if (Array.isArray(node.problems)) {
    count += node.problems.length;
  }
  for (const dependency of Object.values(node.dependencies ?? {})) {
    count += countProblems(dependency, visited);
  }
  return count;
}

const invocation = npmInvocation(["ls", "--all", "--json"]);
const result = spawnSync(invocation.command, invocation.args, {
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024
});

let tree;
try {
  tree = JSON.parse(result.stdout);
} catch {
  console.error("NPM_TREE_FAIL reason=malformed_output");
  process.exit(1);
}

const problems = countProblems(tree);
if (result.error || result.status !== 0 || problems !== 0) {
  console.error("NPM_TREE_FAIL reason=dependency_problem");
  process.exit(1);
}
console.log("NPM_TREE_PASS problems=0");
