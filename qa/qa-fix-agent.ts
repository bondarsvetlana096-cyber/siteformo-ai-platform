import fs from "fs";

function analyze(reportPath: string) {
  const report = JSON.parse(fs.readFileSync(reportPath, "utf-8"));

  const failed = report.filter((r: any) => r.status === "failed");

  if (failed.length === 0) {
    console.log("✅ No issues found");
    return;
  }

  console.log("❌ Issues:");
  failed.forEach((f: any) => {
    console.log("-", f.step);
  });
}

analyze("qa/reports/client-flow-report.json");