import { defineConfig } from "@playwright/test";
import dotenv from "dotenv";

dotenv.config();

export default defineConfig({
  use: {
    headless: process.env.HEADLESS !== "false",
    viewport: { width: 1280, height: 800 },
    trace: "on",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  timeout: 60000,
});