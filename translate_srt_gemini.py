import glob
import json
import os
import re
import sys
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError

MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
]

BATCH_SIZE = 25
REQUEST_DELAY = 2.0

SKIP_PHRASES = (
    "yts.mx",
    "yify",
    "subtitles by",
    "downloaded from",
    "official yify",
    "sync and corrected",
    "www.",
)

SYSTEM_INSTRUCTION = """Jesteś profesjonalnym tłumaczem napisów filmowych (SRT) z języka angielskiego na język polski dla anime Code Geass: Lelouch of the Rebellion.

Zasady:
1. Tłumacz naturalnym, żywym językiem polskim dopasowanym do kontekstu i postaci.
2. Zachowuj nazwy własne ze świata Code Geass (Lelouch, Nunnally, Suzaku, Kururugi, Britannia, Area 11, Knightmare, Knightmare Frame, Geass, Zero, Black Knights, Ashford, Schneizel, Cornelia, Euphemia, C.C., V.V., Rolo, Kallen, Ohgi, Tamaki, Lloyd, Cecile, Jeremiah, Orange, Lancelot, Guren, Burai, Sutherland, Shinkiro, Damocles, F.I.E.I.J.A.). Słowo "Elevens" oddawaj jako "Jedenastki" lub "Eleven" zależnie od kontekstu.
3. Otrzymasz listę obiektów {"id": int, "text": string}. Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{"translations": [{"id": 0, "text": "..."}, ...]}
Zachowaj dokładnie te same wartości "id" i nie łącz ani nie pomijaj żadnego elementu."""


def parse_srt_blocks(content: str):
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized.split("\n\n") if normalized else []


def should_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    return any(phrase in lower for phrase in SKIP_PHRASES)


def load_existing_progress(output_path: str, total_blocks: int):
    if not os.path.exists(output_path):
        return [None] * total_blocks

    with open(output_path, "r", encoding="utf-8", errors="ignore") as f:
        existing_blocks = parse_srt_blocks(f.read())

    result = [None] * total_blocks
    for idx, block in enumerate(existing_blocks):
        if idx >= total_blocks:
            break
        lines = block.split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            result[idx] = block
    return result


def parse_id_map(raw_text: str) -> dict[int, str]:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    data = json.loads(cleaned)

    items = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Brak listy 'translations' w odpowiedzi")

    mapping = {}
    for entry in items:
        if isinstance(entry, dict) and "id" in entry and "text" in entry:
            mapping[int(entry["id"])] = str(entry["text"])
    return mapping


def call_gemini_batch(client: genai.Client, lines: list[str], max_attempts: int = 8) -> list[str]:
    payload_items = [{"id": idx, "text": txt} for idx, txt in enumerate(lines)]
    payload = json.dumps({"items": payload_items}, ensure_ascii=False)

    for attempt in range(1, max_attempts + 1):
        model = MODELS[(attempt - 1) % len(MODELS)]
        try:
            if REQUEST_DELAY > 0:
                time.sleep(REQUEST_DELAY)

            response = client.models.generate_content(
                model=model,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )

            if not response.text:
                raise ValueError("Pusta odpowiedź z API")

            id_map = parse_id_map(response.text)
            missing = [idx for idx in range(len(lines)) if idx not in id_map]
            if missing:
                raise ValueError(f"Brakujące ID w odpowiedzi: {missing}")

            return [id_map[idx] for idx in range(len(lines))]

        except APIError as e:
            wait_time = 4 if e.code in (429, 503) else min(30, 5 * attempt)
            print(
                f"\n[{model} APIError {e.code} próba {attempt}/{max_attempts}] -> przełączam/pauza {wait_time}s",
                flush=True,
            )
            time.sleep(wait_time)
        except Exception as e:
            wait_time = 3
            print(
                f"\n[{model} Błąd paczki próba {attempt}/{max_attempts}] ({e}) -> pauza {wait_time}s",
                flush=True,
            )
            time.sleep(wait_time)

    if len(lines) > 1:
        print("\n[Fallback] Rozbijam paczkę na połówki...", flush=True)
        mid = len(lines) // 2
        return call_gemini_batch(client, lines[:mid], 4) + call_gemini_batch(client, lines[mid:], 4)

    return lines


def translate_srt(client: genai.Client, input_path: str):
    output_path = input_path[:-4] + ".pl.srt" if input_path.endswith(".srt") else input_path + ".pl.srt"
    print(f"\n--- Tłumaczenie: {input_path} -> {output_path} ---", flush=True)

    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_blocks = parse_srt_blocks(f.read())

    total = len(raw_blocks)
    output_blocks = load_existing_progress(output_path, total)
    already_done = sum(1 for b in output_blocks if b is not None)
    if already_done > 0:
        print(f"Wznowiono: gotowych {already_done}/{total} bloków.", flush=True)

    pending_items = []
    parsed_blocks = []

    for i, block in enumerate(raw_blocks):
        lines = block.split("\n")
        if len(lines) < 3 or "-->" not in lines[1]:
            parsed_blocks.append((None, None, []))
            if output_blocks[i] is None:
                output_blocks[i] = block
            continue

        index_line = lines[0].strip()
        time_line = lines[1].strip()
        text_lines = lines[2:]
        parsed_blocks.append((index_line, time_line, text_lines))

        if output_blocks[i] is None:
            for t_idx, line in enumerate(text_lines):
                stripped = line.strip()
                if should_skip_line(stripped):
                    continue
                has_italics = stripped.startswith("<i>") and stripped.endswith("</i>") and len(stripped) > 7
                inner = stripped[3:-4] if has_italics else stripped
                pending_items.append((i, t_idx, has_italics, inner))

    if not pending_items:
        print("Wszystkie bloki są już przetłumaczone.", flush=True)
        return

    translated_map = {}

    for start in range(0, len(pending_items), BATCH_SIZE):
        chunk = pending_items[start : start + BATCH_SIZE]
        translated_texts = call_gemini_batch(client, [item[3] for item in chunk])

        for item, tr_text in zip(chunk, translated_texts):
            b_idx, t_idx, has_italics, _ = item
            translated_map[(b_idx, t_idx)] = f"<i>{tr_text}</i>" if has_italics else tr_text

        for b_idx in {item[0] for item in chunk}:
            index_line, time_line, text_lines = parsed_blocks[b_idx]
            new_text_lines = [
                translated_map.get((b_idx, t_idx), orig_line)
                for t_idx, orig_line in enumerate(text_lines)
            ]
            output_blocks[b_idx] = "\n".join([index_line, time_line] + new_text_lines)

        materialized = [b if b is not None else raw_blocks[idx] for idx, b in enumerate(output_blocks)]
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(materialized) + "\n")

        print(f"Postęp linii: {min(start + BATCH_SIZE, len(pending_items))}/{len(pending_items)}", flush=True)

    print(f"Zakończono: {output_path}", flush=True)


def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("Błąd: brak zmiennej środowiskowej GEMINI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client()
    files = sys.argv[1:] if len(sys.argv) > 1 else [f for f in sorted(glob.glob("*.srt")) if not f.endswith(".pl.srt")]

    for fpath in files:
        translate_srt(client, fpath)


if __name__ == "__main__":
    main()
