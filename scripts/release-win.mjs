import { releaseWindows } from "./release-common.mjs";

try {
  releaseWindows({ resume: process.argv.includes("--resume") });
} catch (error) {
  console.error(error instanceof Error ? error.stack : error);
  process.exitCode = 1;
}
