#!/usr/bin/env python3
"""Generate a comprehensive summary report from failed_txs.json and .env configuration"""

import json
import os
import re
from collections import defaultdict, Counter
from pathlib import Path


def parse_tonemuso_log(filepath="tonemuso_run.log"):
    """Parse tonemuso log file to extract execution statistics"""
    stats = {
        'total_success': 0,
        'total_warnings': 0,
        'total_unsuccess': 0,
        'mc_blocks': 0,
        'shard_blocks': 0,
        'total_txs': 0,
        'from_seqno': None,
        'to_seqno': None,
        'loaded_time': None,
    }
    
    if not os.path.exists(filepath):
        return stats
    
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            
            # Look for "Final emulator status"
            final_match = re.search(r'Final emulator status:\s*(\d+)\s*success,?\s*(\d+)\s*unsuccess,?\s*(\d+)\s*warnings?', content)
            if final_match:
                stats['total_success'] = int(final_match.group(1))
                stats['total_unsuccess'] = int(final_match.group(2))
                stats['total_warnings'] = int(final_match.group(3))
            else:
                # Sum up individual "Emulator status" lines if no final status
                success_sum = 0
                warning_sum = 0
                for match in re.finditer(r'Emulator status:\s*(\d+)\s*success,?\s*(\d+)\s*warnings?,?\s*(\d+)\s*errors?', content):
                    success_sum += int(match.group(1))
                    warning_sum += int(match.group(2))
                stats['total_success'] = success_sum
                stats['total_warnings'] = warning_sum
            
            # Look for "Stats, loaded:"
            stats_match = re.search(r'Stats, loaded:\s*(\d+)\s*MC blocks,\s*(\d+)\s*shard blocks,\s*(\d+)\s*TXs', content)
            if stats_match:
                stats['mc_blocks'] = int(stats_match.group(1))
                stats['shard_blocks'] = int(stats_match.group(2))
                stats['total_txs'] = int(stats_match.group(3))
            
            # Look for seqno range
            seqno_match = re.search(r'Start load historical from seqno:\s*(\d+)', content)
            if seqno_match:
                stats['from_seqno'] = int(seqno_match.group(1))
            
            seqno_end_match = re.search(r'End load historical MASTER to seqno:\s*(\d+)', content)
            if seqno_end_match:
                stats['to_seqno'] = int(seqno_end_match.group(1))
            
            # Look for time info
            time_match = re.search(r'Loaded time\s*([\d:]+)', content)
            if time_match:
                stats['loaded_time'] = time_match.group(1)
    
    except Exception as e:
        print(f"Warning: Could not parse log file: {e}")
    
    return stats


def read_env_file(filepath=".env"):
    """Read active parameters from .env file"""
    active_params = {}
    if not os.path.exists(filepath):
        return active_params
    
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            # Parse export statements
            if line.startswith('export '):
                line = line[7:]  # Remove 'export '
                if '=' in line:
                    key, value = line.split('=', 1)
                    # Remove quotes and comments
                    value = value.split('#')[0].strip()
                    value = value.strip('"').strip("'")
                    active_params[key.strip()] = value
    
    return active_params


def read_failed_txs(filepath="failed_txs.json"):
    """Read failed transactions from JSON file"""
    if not os.path.exists(filepath):
        return []
    
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Warning: Could not parse {filepath}")
        return []


def analyze_errors(failed_txs):
    """Analyze and group errors from failed transactions"""
    error_groups = defaultdict(list)
    fail_reasons = Counter()
    affected_paths_counter = Counter()
    addresses = set()
    account_code_hashes = set()
    
    for tx in failed_txs:
        mode = tx.get('mode', 'unknown')
        fail_reason = tx.get('fail_reason', 'unknown')
        address = tx.get('address', 'unknown')
        
        fail_reasons[fail_reason] += 1
        addresses.add(address)
        
        # Get account code hash
        account_code_hash = tx.get('account_code_hash', 'unknown')
        if account_code_hash != 'unknown':
            account_code_hashes.add(account_code_hash)
        
        # Group by fail reason
        error_groups[fail_reason].append(tx)
        
        # Count affected paths
        if 'color_schema_log' in tx and 'affected_paths' in tx['color_schema_log']:
            for path in tx['color_schema_log']['affected_paths']:
                affected_paths_counter[path] += 1
    
    return {
        'error_groups': error_groups,
        'fail_reasons': fail_reasons,
        'affected_paths': affected_paths_counter,
        'unique_addresses': addresses,
        'unique_code_hashes': account_code_hashes
    }


def generate_report(env_params, failed_txs, analysis, log_stats):
    """Generate formatted report"""
    report = []
    
    # Header
    report.append("=" * 80)
    report.append(" TonTVMReplay Emulation Report")
    report.append("=" * 80)
    report.append("")
    
    # Active Configuration
    report.append("1. ACTIVE CONFIGURATION (.env)")
    report.append("-" * 80)
    
    important_keys = [
        'EMULATOR_PATH', 'EMULATOR_UNCHANGED_PATH',
        'FROM_SEQNO', 'TO_SEQNO',
        'NPROC', 'CHUNK_SIZE', 'TX_CHUNK_SIZE',
        'EMUSO_LOGLEVEL', 'COLOR_SCHEMA_PATH'
    ]
    
    for key in important_keys:
        value = env_params.get(key, 'NOT SET')
        report.append(f"  {key:<30} = {value}")
    
    report.append("")
    
    # Summary Statistics
    report.append("2. EXECUTION SUMMARY")
    report.append("-" * 80)
    
    total_failed = len(failed_txs)
    
    # Use log stats if available
    if log_stats['total_txs'] > 0:
        total_processed = log_stats['total_txs']
        total_passed = log_stats['total_success']
        total_warnings = log_stats['total_warnings']
        # Recalculate failed from actual failed_txs count
        total_failed_actual = total_failed
        
        report.append(f"  Blocks Processed:")
        report.append(f"    - Masterchain Blocks:        {log_stats['mc_blocks']}")
        report.append(f"    - Shard Blocks:              {log_stats['shard_blocks']}")
        report.append(f"    - Total Blocks:              {log_stats['mc_blocks'] + log_stats['shard_blocks']}")
        
        if log_stats['from_seqno'] and log_stats['to_seqno']:
            report.append(f"    - Seqno Range:               {log_stats['from_seqno']} - {log_stats['to_seqno']}")
        
        if log_stats['loaded_time']:
            report.append(f"    - Load Time:                 {log_stats['loaded_time']}")
        
        report.append("")
        report.append(f"  Transactions Processed:")
        report.append(f"    - Total Transactions:        {total_processed}")
        report.append(f"    - Passed (Success):          {total_passed} ({100*total_passed/total_processed:.1f}%)")
        report.append(f"    - Warnings:                  {total_warnings} ({100*total_warnings/total_processed:.1f}%)")
        report.append(f"    - Failed (Errors):           {total_failed_actual} ({100*total_failed_actual/total_processed:.1f}%)")
    else:
        report.append(f"  Failed Transactions:           {total_failed}")
        report.append(f"  (Run statistics not available - log file not found)")
    
    report.append("")
    report.append(f"  Error Analysis:")
    report.append(f"    - Unique Addresses with Errors: {len(analysis['unique_addresses'])}")
    report.append(f"    - Unique Contract Code Hashes:  {len(analysis['unique_code_hashes'])}")
    report.append("")
    
    # Error Grouping by Fail Reason
    report.append("3. ERRORS GROUPED BY FAIL REASON")
    report.append("-" * 80)
    
    for fail_reason, count in analysis['fail_reasons'].most_common():
        percentage = (count / total_failed * 100) if total_failed > 0 else 0
        report.append(f"  {fail_reason:<40} {count:>6} ({percentage:>5.1f}%)")
    
    report.append("")
    
    # Most Affected Paths
    report.append("4. MOST FREQUENTLY AFFECTED TRANSACTION FIELDS")
    report.append("-" * 80)
    
    top_paths = analysis['affected_paths'].most_common(15)
    for path, count in top_paths:
        # Simplify path for readability
        simple_path = path.replace("root['", "").replace("']['", ".").replace("']", "")
        report.append(f"  {simple_path:<70} {count:>6}")
    
    report.append("")
    
    # Detailed Error Breakdown
    report.append("5. ERROR DETAILS BY FAIL REASON")
    report.append("-" * 80)
    
    for fail_reason, txs in sorted(analysis['error_groups'].items(), key=lambda x: len(x[1]), reverse=True):
        report.append(f"\n  Fail Reason: {fail_reason} ({len(txs)} occurrences)")
        report.append("  " + "-" * 76)
        
        # Group by address
        address_count = Counter(tx.get('address', 'unknown') for tx in txs)
        for addr, count in address_count.most_common(10):
            report.append(f"    Address: {addr}")
            report.append(f"    Count:   {count}")
            
            # Show one example for this address
            example = next(tx for tx in txs if tx.get('address') == addr)
            
            # Show some diff details
            if 'color_schema_log' in example and 'colors' in example['color_schema_log']:
                colors = example['color_schema_log']['colors']
                alarm_fields = [k for k, v in colors.items() if v == 'alarm']
                if alarm_fields:
                    report.append(f"    Alarm Fields: {', '.join(alarm_fields[:5])}")
                    if len(alarm_fields) > 5:
                        report.append(f"                  ... and {len(alarm_fields) - 5} more")
            
            report.append("")
            
            if len(address_count) > 10:
                report.append(f"    ... and {len(address_count) - 10} more addresses")
                break
    
    report.append("")
    report.append("=" * 80)
    report.append(" End of Report")
    report.append("=" * 80)
    
    return "\n".join(report)


def main():
    """Main function to generate the report"""
    # Read configuration and failed transactions
    env_params = read_env_file(".env")
    failed_txs = read_failed_txs("failed_txs.json")
    
    if not failed_txs:
        print("\n" + "=" * 80)
        print("No failed transactions found or failed_txs.json does not exist.")
        print("This means all transactions passed successfully! ✓")
        print("=" * 80 + "\n")
        return
    
    # Analyze errors
    analysis = analyze_errors(failed_txs)
    
    # Parse log statistics
    log_stats = parse_tonemuso_log()
    
    # Generate report
    report = generate_report(env_params, failed_txs, analysis, log_stats)
    
    # Print to console
    print("\n" + report + "\n")
    
    # Save to file
    output_file = "emulation_report.txt"
    with open(output_file, 'w') as f:
        f.write(report)
    
    print(f"Report saved to: {output_file}")
    
    # Also create pretty JSON for reference
    with open("failed_txs_pretty.json", "w") as f:
        json.dump(failed_txs, f, indent=2)
    
    print(f"Detailed JSON saved to: failed_txs_pretty.json")


if __name__ == "__main__":
    main()
