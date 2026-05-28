import json
from pathlib import Path
from collections import Counter

def analyze_error_files(root_dir):
    counter = Counter()
    files_processed = 0
    files_skipped = 0
    
    for path in Path(root_dir).rglob("error_info.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            values_changed = (
                data.get("diff", {})
                    .get("transaction", {})
                    .get("values_changed", {})
            )
            
            for key in values_changed.keys():
                counter[key] += 1
            
            files_processed += 1
        except (json.JSONDecodeError, OSError) as e:
            print(f"Skipped {path}: {e}")
            files_skipped += 1
    
    return counter, files_processed, files_skipped


def print_report(counter, files_processed, files_skipped):
    print(f"Files processed: {files_processed}")
    print(f"Files skipped:   {files_skipped}")
    print(f"Unique elements: {len(counter)}\n")
    print(f"{'Count':>8}  Element")
    print("-" * 80)
    for element, count in counter.most_common():
        print(f"{count:>8}  {element}")


if __name__ == "__main__":
    counter, processed, skipped = analyze_error_files(".")  # or your path
    print_report(counter, processed, skipped)
    
    # Optional: save as CSV
    import csv
    with open("report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["element", "count"])
        for element, count in counter.most_common():
            writer.writerow([element, count])
