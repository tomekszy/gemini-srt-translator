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
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-2.5-flash-lite",
]

BATCH_SIZE = 25
REQUEST_DELAY = 2.0
KEYWORDS_FILENAME = "keywords.json"

SKIP_PHRASES = (
    "yts.mx",
    "yify",
    "subtitles by",
    "downloaded from",
    "official yify",
    "sync and corrected",
    "www.",
)

PROMPT_TEMPLATE = """Jesteś profesjonalnym tłumaczem napisów filmowych (SRT) z języka angielskiego na język polski dla filmu: {title}.

Zasady:
1. Tłumacz naturalnym, żywym językiem polskim dopasowanym do kontekstu i postaci.
2. Zachowuj nazwy własne ze świata tego filmu: {proper_nouns}. Dopasowanie nazw własnych jest niewrażliwe na wielkość liter (case-insensitive) – jeśli w tekście źródłowym wystąpi nazwa z tej listy zapisana inną wielkością liter, zachowaj ją w podanej formie.
3. Otrzymasz listę obiektów {{"id": int, "text": string}}. Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{{"translations": [{{"id": 0, "text": "..."}}, ...]}}
Zachowaj dokładnie te same wartości "id" i nie łącz ani nie pomijaj żadnego elementu."""


def build_system_instruction(title: str, keywords: list[str]) -> str:
    clean_title = title.strip() if title.strip() else "nieokreślony tytuł"
    clean_keywords = [k.strip() for k in keywords if k.strip()]
    formatted_nouns = ", ".join(clean_keywords) if clean_keywords else "brak"
    return PROMPT_TEMPLATE.format(title=clean_title, proper_nouns=formatted_nouns)


def find_keywords_file(input_files: list[str]) -> str | None:
    candidates = [os.path.join(os.getcwd(), KEYWORDS_FILENAME)]
    if input_files:
        first_dir = os.path.dirname(os.path.abspath(input_files[0]))
        candidates.append(os.path.join(first_dir, KEYWORDS_FILENAME))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, KEYWORDS_FILENAME))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_config_from_json(json_path: str) -> tuple[str, list[str]]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    title = str(data.get("title", "")).strip()
    raw_keywords = data.get("keywords", [])
    if not isinstance(raw_keywords, list):
        raise ValueError("Pole 'keywords' w keywords.json musi być listą.")
    keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
    return title, keywords


def prompt_translation_config(input_files: list[str]) -> tuple[str, list[str], int]:
    print("\n--- Konfiguracja tłumaczenia ---", flush=True)

    json_path = find_keywords_file(input_files)
    if json_path:
        try:
            title, keywords = load_config_from_json(json_path)
            print(f"Znaleziono plik konfiguracyjny: {json_path}", flush=True)
            print(f"  Tytuł filmu: {title or '(brak)'}", flush=True)
            print(
                f"  Zachowywane nazwy własne ({len(keywords)}): {', '.join(keywords) if keywords else '(brak)'}",
                flush=True,
            )
        except Exception as e:
            print(f"Błąd odczytu {json_path}: {e}. Przechodzę do trybu ręcznego.", flush=True)
            json_path = None

    if not json_path:
        title = input("Jaki film tłumaczymy?: ").strip()
        proper_nouns_raw = input(
            "Jakie nazwy własne ze świata tego filmu mają zostać zachowane (po przecinku, case-insensitive)?: "
        ).strip()
        keywords = [n.strip() for n in proper_nouns_raw.split(",") if n.strip()]

    start_raw = input(
        "Od którego numeru napisu (bloku SRT) tłumaczymy plik? [Enter = od początku (0)]: "
    ).strip()

    if not start_raw:
        start_number = 0
    else:
        try:
            start_number = max(0, int(start_raw))
        except ValueError:
            print("Nieprawidłowy numer – rozpoczynam od początku (0).", flush=True)
            start_number = 0

    return title, keywords, start_number


def parse_srt_blocks(content: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized.split("\n\n") if normalized else []


def extract_block_number(block: str, fallback_index: int) -> int:
    first_line = block.split("\n", 1)[0].strip().lstrip("\ufeff")
    if first_line.isdigit():
        return int(first_line)
    return fallback_index + 1


def should_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    return any(phrase in lower for phrase in SKIP_PHRASES)


def load_existing_blocks_by_number(output_path: str) -> dict[int, str]:
    if not os.path.exists(output_path):
        return {}

    with open(output_path, "r", encoding="utf-8", errors="ignore") as f:
        existing_blocks = parse_srt_blocks(f.read())

    by_num: dict[int, str] = {}
    for idx, block in enumerate(existing_blocks):
        lines = block.split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            num = extract_block_number(block, idx)
            by_num[num] = block
    return by_num


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


def call_gemini_batch(
    client: genai.Client,
    system_instruction: str,
    lines: list[str],
    max_attempts: int = 40,
) -> list[str]:
    payload_items = [{"id": idx, "text": txt} for idx, txt in enumerate(lines)]
    payload = json.dumps({"items": payload_items}, ensure_ascii=False)
    active_models = list(MODELS)
    model_idx = 0

    for attempt in range(1, max_attempts + 1):
        if not active_models:
            print("\n[Brak dostępnych modeli] Wszystkie modele zwróciły 404.", flush=True)
            break

        model = active_models[model_idx % len(active_models)]
        try:
            if REQUEST_DELAY > 0:
                time.sleep(REQUEST_DELAY)

            response = client.models.generate_content(
                model=model,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
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
            if e.code == 404:
                print(
                    f"\n[{model} APIError 404] Model niedostępny – usuwam z listy i pomijam oczekiwanie.",
                    flush=True,
                )
                active_models.remove(model)
                continue

            wait_time = 4 if e.code in (429, 503) else min(30, 5 * attempt)
            print(
                f"\n[{model} APIError {e.code} próba {attempt}/{max_attempts}] -> przełączam/pauza {wait_time}s",
                flush=True,
            )
            model_idx += 1
            time.sleep(wait_time)
        except Exception as e:
            wait_time = 3
            print(
                f"\n[{model} Błąd paczki próba {attempt}/{max_attempts}] ({e}) -> pauza {wait_time}s",
                flush=True,
            )
            model_idx += 1
            time.sleep(wait_time)

    if len(lines) > 1 and active_models:
        print("\n[Fallback] Rozbijam paczkę na połówki...", flush=True)
        mid = len(lines) // 2
        return (
            call_gemini_batch(client, system_instruction, lines[:mid], 8)
            + call_gemini_batch(client, system_instruction, lines[mid:], 8)
        )

    return lines


def translate_srt(
    client: genai.Client,
    system_instruction: str,
    input_path: str,
    start_number: int = 0,
):
    output_path = input_path[:-4] + ".pl.srt" if input_path.endswith(".srt") else input_path + ".pl.srt"
    print(f"\n--- Tłumaczenie: {input_path} -> {output_path} ---", flush=True)

    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_blocks = parse_srt_blocks(f.read())

    total = len(raw_blocks)
    output_blocks: list[str | None] = [None] * total
    existing_by_num = load_existing_blocks_by_number(output_path)

    if start_number > 1:
        kept_count = 0
        for i, block in enumerate(raw_blocks):
            block_num = extract_block_number(block, i)
            if block_num < start_number:
                if block_num in existing_by_num:
                    output_blocks[i] = existing_by_num[block_num]
                    kept_count += 1
                else:
                    output_blocks[i] = block
        print(
            f"Zachowano {kept_count} przetłumaczonych bloków sprzed numeru {start_number}. "
            f"Tłumaczenie startuje od bloku #{start_number}.",
            flush=True,
        )
    else:
        print("Rozpoczynam tłumaczenie od początku (blok #1).", flush=True)

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
        print("Brak linii do przetłumaczenia.", flush=True)
        return

    translated_map = {}

    for start in range(0, len(pending_items), BATCH_SIZE):
        chunk = pending_items[start : start + BATCH_SIZE]
        translated_texts = call_gemini_batch(
            client,
            system_instruction,
            [item[3] for item in chunk],
        )

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

    files = sys.argv[1:] if len(sys.argv) > 1 else [f for f in sorted(glob.glob("*.srt")) if not f.endswith(".pl.srt")]

    if not files:
        print("Brak plików .srt do przetłumaczenia.")
        return

    title, keywords, start_number = prompt_translation_config(files)
    system_instruction = build_system_instruction(title, keywords)

    client = genai.Client()

    for idx, fpath in enumerate(files):
        # Numer startowy dotyczy pierwszego pliku; kolejne pliki z listy lecą od zera
        file_start = start_number if idx == 0 else 0
        translate_srt(client, system_instruction, fpath, start_number=file_start)


if __name__ == "__main__":
    main()
