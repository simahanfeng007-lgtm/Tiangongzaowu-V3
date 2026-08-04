import { finalizeWindowsStage } from "./release-common.mjs";

try {
  finalizeWindowsStage();
} catch (error) {
  console.error(error instanceof Error ? error.stack : error);
  process.exitCode = 1;
}
