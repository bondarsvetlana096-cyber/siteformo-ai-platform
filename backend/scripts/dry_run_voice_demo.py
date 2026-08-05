from __future__ import annotations

import json

from app.services.voice_delivery.twiml import render_twiml, spoken_script


def main() -> int:
    result = {
        "provider_call_count": 0,
        "script": spoken_script("Oleh"),
        "twiml": render_twiml("Oleh", voice="Polly.Amy-Neural", language="en-GB"),
        "delay_seconds": 7,
        "retry": False,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
