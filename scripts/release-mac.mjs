import { releaseMac } from "./release-common.mjs";

try {
  releaseMac();
} catch (error) {
  console.error(error instanceof Error ? error.stack : error);
  process.exitCode = 1;
}

