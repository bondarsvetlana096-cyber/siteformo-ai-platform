import dotenv from "dotenv";
import OpenAI from "openai";

dotenv.config();

export async function aiFallback(html: string) {
  if (!process.env.OPENAI_API_KEY) {
    return {
      action: "skip",
      target: "",
      reason: "OPENAI_API_KEY is missing",
    };
  }

  const openai = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
  });

  const res = await openai.chat.completions.create({
    model: "gpt-4.1-mini",
    messages: [
      {
        role: "system",
        content: `
You are a QA automation agent.
Return JSON only:
{ "action": "click", "target": "text" }
        `,
      },
      {
        role: "user",
        content: html.slice(0, 4000),
      },
    ],
  });

  return JSON.parse(res.choices[0].message.content || "{}");
}