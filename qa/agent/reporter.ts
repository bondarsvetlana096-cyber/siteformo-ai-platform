import fs from "fs";

const logs: any[] = [];

export function log(step: string, status: string, details?: any) {
  console.log(`${status === "passed" ? "✅" : "❌"} ${step}`);
  logs.push({ step, status, details });
}

export function saveReport() {
  fs.mkdirSync("qa/reports", { recursive: true });
  fs.writeFileSync(
    "qa/reports/report.json",
    JSON.stringify(logs, null, 2)
  );
}