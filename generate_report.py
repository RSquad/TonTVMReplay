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
        'unique_accounts': 0,
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
            
            # Look for "Total unique accounts processed"
            accounts_match = re.search(r'Total unique accounts processed:\s*(\d+)', content)
            if accounts_match:
                stats['unique_accounts'] = int(accounts_match.group(1))
            
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


def generate_html_report(env_params, failed_txs, analysis, log_stats):
    """Generate HTML formatted report"""
    from datetime import datetime
    
    html = []
    
    # HTML Header with CSS
    html.append("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TonTVMReplay Emulation Report</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            line-height: 1.6;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header .timestamp { opacity: 0.9; font-size: 0.9em; }
        .content { padding: 40px; }
        .section {
            margin-bottom: 40px;
            border-left: 4px solid #667eea;
            padding-left: 20px;
        }
        .section h2 {
            color: #667eea;
            margin-bottom: 20px;
            font-size: 1.8em;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        .stat-card {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .stat-card h3 {
            color: #555;
            font-size: 0.9em;
            margin-bottom: 10px;
            text-transform: uppercase;
        }
        .stat-card .value {
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }
        .stat-card .subvalue {
            font-size: 0.9em;
            color: #666;
            margin-top: 5px;
        }
        .config-table {
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }
        .config-table th {
            background: #667eea;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }
        .config-table td {
            padding: 10px 12px;
            border-bottom: 1px solid #e0e0e0;
        }
        .config-table tr:hover { background: #f5f5f5; }
        .config-group {
            margin: 20px 0;
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
        }
        .config-group h4 {
            color: #667eea;
            margin-bottom: 10px;
        }
        .error-list {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 6px;
            margin: 15px 0;
        }
        .error-item {
            background: white;
            padding: 15px;
            margin: 10px 0;
            border-radius: 6px;
            border-left: 4px solid #e74c3c;
        }
        .error-item h4 {
            color: #e74c3c;
            margin-bottom: 10px;
        }
        .path-list {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
        }
        .path-item {
            padding: 8px;
            margin: 5px 0;
            background: white;
            border-radius: 4px;
            display: flex;
            justify-content: space-between;
        }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }
        .badge-success { background: #27ae60; color: white; }
        .badge-warning { background: #f39c12; color: white; }
        .badge-error { background: #e74c3c; color: white; }
        .progress-bar {
            width: 100%;
            height: 30px;
            background: #e0e0e0;
            border-radius: 15px;
            overflow: hidden;
            margin: 10px 0;
        }
        .progress-segment {
            float: left;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 0.85em;
        }
        .footer {
            background: #2c3e50;
            color: white;
            text-align: center;
            padding: 20px;
            font-size: 0.9em;
        }
        code {
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 TonTVMReplay Emulation Report</h1>
            <p class="timestamp">Generated: {timestamp}</p>
        </div>
        <div class="content">
""".format(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    
    total_failed = len(failed_txs)
    total_processed = log_stats['total_txs'] if log_stats['total_txs'] > 0 else 0
    total_passed = log_stats['total_success']
    total_warnings = log_stats['total_warnings']
    total_blocks = log_stats['mc_blocks'] + log_stats['shard_blocks']
    unique_accounts_with_errors = len(analysis['unique_addresses'])
    total_unique_accounts = log_stats['unique_accounts']
    
    # Summary Statistics Section
    html.append("""
            <div class="section">
                <h2>📊 Execution Summary</h2>
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>Total Blocks</h3>
                        <div class="value">{total_blocks}</div>
                        <div class="subvalue">MC: {mc_blocks} | Shards: {shard_blocks}</div>
                    </div>
                    <div class="stat-card">
                        <h3>Total Transactions</h3>
                        <div class="value">{total_txs}</div>
                        <div class="subvalue">Processed</div>
                    </div>
                    <div class="stat-card">
                        <h3>Total Accounts</h3>
                        <div class="value">{total_accounts}</div>
                        <div class="subvalue">Unique addresses</div>
                    </div>
                    <div class="stat-card">
                        <h3>Accounts w/ Errors</h3>
                        <div class="value">{error_accounts}</div>
                        <div class="subvalue">Failed transactions</div>
                    </div>
                    <div class="stat-card">
                        <h3>Contract Types</h3>
                        <div class="value">{unique_hashes}</div>
                        <div class="subvalue">Code hashes</div>
                    </div>
                </div>
""".format(
        total_blocks=total_blocks,
        mc_blocks=log_stats['mc_blocks'],
        shard_blocks=log_stats['shard_blocks'],
        total_txs=total_processed if total_processed > 0 else 'N/A',
        total_accounts=total_unique_accounts if total_unique_accounts > 0 else 'N/A',
        error_accounts=unique_accounts_with_errors,
        unique_hashes=len(analysis['unique_code_hashes'])
    ))
    
    # Block range info
    if log_stats['from_seqno'] and log_stats['to_seqno']:
        html.append("""
                <p style="margin-top: 20px;"><strong>Block Range:</strong> Seqno {from_seqno} → {to_seqno}</p>
""".format(from_seqno=log_stats['from_seqno'], to_seqno=log_stats['to_seqno']))
    
    if log_stats['loaded_time']:
        html.append("""
                <p><strong>Load Time:</strong> {load_time}</p>
""".format(load_time=log_stats['loaded_time']))
    
    # Transaction results breakdown
    if total_processed > 0:
        success_pct = (total_passed / total_processed * 100)
        warning_pct = (total_warnings / total_processed * 100)
        error_pct = (total_failed / total_processed * 100)
        
        html.append("""
                <h3 style="margin-top: 30px; color: #555;">Transaction Results</h3>
                <div class="progress-bar">
                    <div class="progress-segment" style="width: {success_pct}%; background: #27ae60;">
                        {success_count} Success
                    </div>
                    <div class="progress-segment" style="width: {warning_pct}%; background: #f39c12;">
                        {warning_count} Warn
                    </div>
                    <div class="progress-segment" style="width: {error_pct}%; background: #e74c3c;">
                        {error_count} Error
                    </div>
                </div>
                <div style="display: flex; justify-content: space-around; margin-top: 15px;">
                    <div>
                        <span class="badge badge-success">{success_count} Success ({success_pct:.1f}%)</span>
                    </div>
                    <div>
                        <span class="badge badge-warning">{warning_count} Warnings ({warning_pct:.1f}%)</span>
                    </div>
                    <div>
                        <span class="badge badge-error">{error_count} Errors ({error_pct:.1f}%)</span>
                    </div>
                </div>
            </div>
""".format(
            success_pct=success_pct,
            warning_pct=warning_pct,
            error_pct=error_pct,
            success_count=total_passed,
            warning_count=total_warnings,
            error_count=total_failed
        ))
    else:
        html.append("""
                <p style="color: #e74c3c; margin-top: 20px;"><strong>Note:</strong> Run statistics not available - log file not found</p>
            </div>
""")
    
    # Active Configuration Section
    html.append("""
            <div class="section">
                <h2>⚙️ Active Configuration</h2>
""")
    
    
    # Define env var groups in logical order (only shown if set)
    env_groups = [
        ('Liteserver Configuration', [
            'LITESERVER_SERVER', 'LITESERVER_PORT', 'LITESERVER_PUBKEY',
            'LITESERVER_TIMEOUT'
        ]),
        ('Emulator Paths', [
            'EMULATOR_PATH', 'EMULATOR_UNCHANGED_PATH'
        ]),
        ('Block Range', [
            'FROM_SEQNO', 'TO_SEQNO', 'TO_EMULATE_MC_BLOCKS'
        ]),
        ('Toncenter API', [
            'TONCENTER_API', 'TONCENTER_API_KEY',
            'TONCENTER_TX_HASH', 'TONCENTER_MSG_HASH',
            'TONCENTER_TRACES_BY_MASTERS'
        ]),
        ('Processing Mode', [
            'TXS_TO_PROCESS_PATH', 'ONLYMC_BLOCK', 'PARSE_OVER_LS'
        ]),
        ('Performance', [
            'NPROC', 'CHUNK_SIZE', 'TX_CHUNK_SIZE'
        ]),
        ('Advanced', [
            'COLOR_SCHEMA_PATH', 'C7_REWRITE', 'EMUSO_LOGLEVEL'
        ])
    ]
    
    # Collect numbered liteserver configs (1-99)
    numbered_liteservers = []
    for i in range(1, 100):
        if f'LITESERVER_SERVER_{i}' in env_params:
            numbered_liteservers.extend([
                f'LITESERVER_SERVER_{i}',
                f'LITESERVER_PORT_{i}',
                f'LITESERVER_PUBKEY_{i}'
            ])
    
    if numbered_liteservers:
        env_groups.insert(0, ('Liteserver Failover', numbered_liteservers))
    
    # Print each group
    for group_name, keys in env_groups:
        # Check if any key in this group is set
        group_values = [(key, env_params.get(key)) for key in keys if key in env_params]
        
        if group_values:
            html.append("""
                <div class="config-group">
                    <h4>{group_name}</h4>
                    <table class="config-table">
""".format(group_name=group_name))
            for key, value in group_values:
                # Escape HTML and truncate long values
                value_display = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                if len(value_display) > 100:
                    value_display = value_display[:97] + '...'
                html.append("""
                        <tr><td style="width: 40%;"><strong>{key}</strong></td><td><code>{value}</code></td></tr>
""".format(key=key, value=value_display))
            html.append("""
                    </table>
                </div>
""")
    
    # Show any other env vars not in predefined groups
    all_known_keys = set()
    for _, keys in env_groups:
        all_known_keys.update(keys)
    
    other_keys = sorted([k for k in env_params.keys() if k not in all_known_keys])
    if other_keys:
        html.append("""
                <div class="config-group">
                    <h4>Other Parameters</h4>
                    <table class="config-table">
""")
        for key in other_keys:
            value_display = str(env_params[key]).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            if len(value_display) > 100:
                value_display = value_display[:97] + '...'
            html.append("""
                        <tr><td style="width: 40%;"><strong>{key}</strong></td><td><code>{value}</code></td></tr>
""".format(key=key, value=value_display))
        html.append("""
                    </table>
                </div>
""")
    
    html.append("""
            </div>
""")
    
    # Error Grouping by Fail Reason
    html.append("""
            <div class="section">
                <h2>🚨 Errors Grouped by Fail Reason</h2>
""")
    
    if total_failed == 0:
        html.append("""
                <p style="color: #27ae60; font-size: 1.2em;">✓ No errors detected - all transactions passed successfully!</p>
            </div>
""")
    else:
        html.append("""
                <div class="error-list">
""")
        for fail_reason, count in analysis['fail_reasons'].most_common():
            percentage = (count / total_failed * 100) if total_failed > 0 else 0
            html.append("""
                    <div style="display: flex; justify-content: space-between; padding: 10px; background: white; margin: 5px 0; border-radius: 4px;">
                        <span><strong>{reason}</strong></span>
                        <span><span class="badge badge-error">{count}</span> <span style="color: #666;">({pct:.1f}%)</span></span>
                    </div>
""".format(reason=fail_reason, count=count, pct=percentage))
        html.append("""
                </div>
            </div>
""")
    
    # Most Affected Paths
    html.append("""
            <div class="section">
                <h2>📍 Most Frequently Affected Transaction Fields</h2>
""")
    
    if total_failed == 0:
        html.append("""
                <p style="color: #27ae60;">No errors to analyze.</p>
            </div>
""")
    else:
        top_paths = analysis['affected_paths'].most_common(20)
        html.append("""
                <div class="path-list">
""")
        for path, count in top_paths:
            # Simplify path for readability
            simple_path = path.replace("root['", "").replace("']['", ".").replace("']", "")
            simple_path = simple_path.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html.append("""
                    <div class="path-item">
                        <span>{path}</span>
                        <span class="badge badge-error">{count}</span>
                    </div>
""".format(path=simple_path, count=count))
        html.append("""
                </div>
            </div>
""")
    
    # Detailed Error Breakdown
    html.append("""
            <div class="section">
                <h2>📋 Error Details by Fail Reason</h2>
""")
    
    if total_failed == 0:
        html.append("""
                <p style="color: #27ae60;">No errors detected.</p>
            </div>
""")
    else:
        for fail_reason, txs in sorted(analysis['error_groups'].items(), key=lambda x: len(x[1]), reverse=True):
            html.append("""
                <div class="error-item">
                    <h4>{reason} ({count} occurrences)</h4>
""".format(reason=fail_reason, count=len(txs)))
            
            # Group by address
            address_count = Counter(tx.get('address', 'unknown') for tx in txs)
            for idx, (addr, count) in enumerate(address_count.most_common(10)):
                html.append("""
                    <div style="background: #f8f9fa; padding: 10px; margin: 10px 0; border-radius: 4px;">
                        <p style="margin-bottom: 5px;"><strong>Address:</strong> <code>{addr}</code></p>
                        <p style="margin-bottom: 5px;"><strong>Count:</strong> {count}</p>
""".format(addr=addr, count=count))
                
                # Show one example for this address
                example = next(tx for tx in txs if tx.get('address') == addr)
                
                # Show some diff details
                if 'color_schema_log' in example and 'colors' in example['color_schema_log']:
                    colors = example['color_schema_log']['colors']
                    alarm_fields = [k for k, v in colors.items() if v == 'alarm']
                    if alarm_fields:
                        alarm_display = ', '.join(alarm_fields[:5])
                        alarm_display = alarm_display.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        html.append("""
                        <p><strong>Alarm Fields:</strong> <code>{fields}</code></p>
""".format(fields=alarm_display))
                        if len(alarm_fields) > 5:
                            html.append("""
                        <p style="color: #666; font-size: 0.9em;">... and {more} more</p>
""".format(more=len(alarm_fields) - 5))
                
                html.append("""
                    </div>
""")
                
                if idx >= 9 and len(address_count) > 10:
                    html.append("""
                    <p style="color: #666; font-style: italic;">... and {more} more addresses</p>
""".format(more=len(address_count) - 10))
                    break
            
            html.append("""
                </div>
""")
        html.append("""
            </div>
""")
    
    # Close HTML
    html.append("""
        </div>
        <div class="footer">
            <p>Generated by TonTVMReplay Report Generator</p>
            <p>© 2024 Disintar LLP | Licensed under Apache License 2.0</p>
        </div>
    </div>
</body>
</html>
""")
    
    return "\n".join(html)


def main():
    """Main function to generate the report"""
    # Read configuration and failed transactions
    env_params = read_env_file(".env")
    failed_txs = read_failed_txs("failed_txs.json")
    
    # Parse log statistics
    log_stats = parse_tonemuso_log()
    
    # Analyze errors (will be empty if no failures)
    analysis = analyze_errors(failed_txs)
    
    # Generate HTML report
    report = generate_html_report(env_params, failed_txs, analysis, log_stats)
    
    # Save to file
    output_file = "emulation_report.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✓ HTML Report saved to: {output_file}")
    
    # Print summary to console
    total_processed = log_stats['total_txs'] if log_stats['total_txs'] > 0 else 0
    total_passed = log_stats['total_success']
    total_warnings = log_stats['total_warnings']
    total_failed = len(failed_txs)
    total_blocks = log_stats['mc_blocks'] + log_stats['shard_blocks']
    total_unique_accounts = log_stats['unique_accounts']
    
    print(f"\n{'='*60}")
    print(f"  📊 Emulation Summary")
    print(f"{'='*60}")
    print(f"  Blocks:        {total_blocks} (MC: {log_stats['mc_blocks']}, Shards: {log_stats['shard_blocks']})")
    print(f"  Transactions:  {total_processed if total_processed > 0 else 'N/A'}")
    print(f"  Accounts:      {total_unique_accounts if total_unique_accounts > 0 else 'N/A'} total unique")
    print(f"  Success:       {total_passed}")
    print(f"  Warnings:      {total_warnings}")
    print(f"  Errors:        {total_failed}")
    if total_unique_accounts > 0:
        print(f"  Errors/Acct:   {len(analysis['unique_addresses'])} accounts with errors")
    print(f"{'='*60}\n")
    
    # Print additional message if no errors
    if not failed_txs:
        print("✓ No errors detected - all transactions passed successfully!")
        print("")
    
    # Also create pretty JSON for reference (only if there are failures)
    if failed_txs:
        with open("failed_txs_pretty.json", "w") as f:
            json.dump(failed_txs, f, indent=2)
        
        print(f"Detailed JSON saved to: failed_txs_pretty.json\n")


if __name__ == "__main__":
    main()
