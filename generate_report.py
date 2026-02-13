#!/usr/bin/env python3
"""Generate a readable report from failed_txs.json"""

import json
from collections import Counter


def main():
    try:
        with open("failed_txs.json", "r") as f:
            failed_txs = json.load(f)
    except FileNotFoundError:
        print("Error: failed_txs.json not found. Run tonemuso first.")
        return
    
    # Create detailed report
    with open("failed_txs_report.txt", "w") as f:
        f.write("=" * 80 + "\n")
        f.write("TonTVMReplay Failed Transactions Report\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"Total failed transactions: {len(failed_txs)}\n\n")
        
        # Count by address
        address_counts = Counter()
        for tx in failed_txs:
            address_counts[tx['address']] += 1
        
        f.write("Failed transactions by address:\n")
        f.write("-" * 80 + "\n")
        for addr, count in address_counts.most_common():
            f.write(f"{addr}: {count} failures\n")
        
        f.write("\n" + "=" * 80 + "\n")
        f.write("Detailed Transaction Information\n")
        f.write("=" * 80 + "\n\n")
        
        for idx, tx in enumerate(failed_txs, 1):
            f.write(f"\nTransaction #{idx}\n")
            f.write("-" * 80 + "\n")
            f.write(f"Address: {tx['address']}\n")
            f.write(f"Mode: {tx['mode']}\n")
            f.write(f"Fail Reason: {tx['fail_reason']}\n")
            f.write(f"Account Code Hash: {tx['account_code_hash']}\n")
            f.write(f"Expected TX Hash: {tx['expected']}\n")
            f.write(f"Got TX Hash: {tx['got']}\n")
            
            if 'color_schema_log' in tx:
                affected = tx['color_schema_log'].get('affected_paths', [])
                f.write(f"\nAffected paths ({len(affected)}):\n")
                for path in affected[:10]:  # Show first 10
                    f.write(f"  - {path}\n")
                if len(affected) > 10:
                    f.write(f"  ... and {len(affected) - 10} more\n")
    
    # Create pretty JSON
    with open("failed_txs_pretty.json", "w") as f:
        json.dump(failed_txs, f, indent=2)
    
    print("✓ Reports generated:")
    print(f"  - failed_txs_report.txt (readable summary)")
    print(f"  - failed_txs_pretty.json (formatted JSON)")
    print(f"\nTotal failed transactions: {len(failed_txs)}")
    print(f"Unique addresses with failures: {len(address_counts)}")


if __name__ == "__main__":
    main()
