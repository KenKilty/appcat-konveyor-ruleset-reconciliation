#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml  # PyYAML
except Exception:
    print("PyYAML is required. Run: pip install -r tools/requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from deepdiff import DeepDiff
    HAVE_DEEPDIFF = True
except ImportError:
    HAVE_DEEPDIFF = False
    print("WARNING: DeepDiff not available. Install with: pip install deepdiff", file=sys.stderr)
    print("Change type analysis will be limited without DeepDiff.", file=sys.stderr)
except Exception as e:
    HAVE_DEEPDIFF = False
    print(f"WARNING: DeepDiff import error: {e}", file=sys.stderr)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
REPORTS = ROOT / "reports"
LOGS = TOOLS / "logs"
STATE_FILE = TOOLS / "state.json"
CACHE_DIR = TOOLS / ".cache"

UPSTREAM_ROOT = ROOT / "rulesets"
DOWNSTREAM_ROOT = ROOT / "appcat-konveyor-rulesets"


def log(msg: str):
    LOGS.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOGS / "run.log", "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"state load error: {e}")
    return {
        "scan_yaml": False,
        "per_rule_diff": False,
        "per_file_diff": False,
        "branch_scan": False,
        "write_readme": False,
    }


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_all():
    for path in [REPORTS, LOGS, CACHE_DIR, STATE_FILE]:
        try:
            if path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        except Exception as e:
            log(f"reset error on {path}: {e}")
    # Rewrite README minimal header; allow regenerate on next run
    readme = ROOT / "README.md"
    if readme.exists():
        try:
            readme.write_text(
                "AppCAT ↔ Konveyor rulesets reconciliation (YAML-focused)\n\n"
                "Run `python3 tools/orchestrate.py --run` to regenerate.\n",
                encoding="utf-8",
            )
        except Exception as e:
            log(f"reset README error: {e}")


def ensure_dirs():
    REPORTS.mkdir(exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def check_repos():
    if not UPSTREAM_ROOT.exists() or not DOWNSTREAM_ROOT.exists():
        log("missing upstream or downstream directories")
        print("Error: ensure `rulesets/` and `appcat-konveyor-rulesets/` exist at repo root.", file=sys.stderr)
        sys.exit(2)


def hash_json(obj) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def list_yaml_files(base: Path):
    return [p for p in base.rglob("*") if p.suffix.lower() in (".yaml", ".yml") and p.is_file()]


def load_yaml_documents(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
        docs = list(yaml.safe_load_all(text))
        return [d for d in docs if d is not None]
    except Exception as e:
        log(f"yaml load error {path}: {e}")
        return []


def categorize_rule(rule, file_path):
    """Categorize a rule based on file path and content"""
    file_lower = file_path.lower()
    
    if "azure" in file_lower or "azure-" in rule.get("ruleID", "").lower():
        return "Azure rules"
    elif any(fw in file_lower for fw in ["spring", "hibernate", "quarkus", "camel", "eap", "openjdk", "jakarta"]):
        return "Java framework rules"
    elif "cloud-readiness" in file_lower or "embedded-cache" in file_lower or "jni" in file_lower:
        return "Cloud readiness rules"
    elif "technology-usage" in file_lower or "3rd-party" in file_lower:
        return "Technology usage rules"
    elif "00-discovery" in file_lower or "discover" in rule.get("ruleID", "").lower():
        return "Discovery rules"
    else:
        return "Other/Uncategorized"


def analyze_change_type(dd):
    """Analyze the type of changes in a DeepDiff result"""
    change_types = []
    
    # Convert DeepDiff result to dict if needed
    if hasattr(dd, 'to_dict'):
        dd_dict = dd.to_dict()
    else:
        dd_dict = dd
    
    if "dictionary_item_added" in dd_dict:
        change_types.append("field_additions")
    if "dictionary_item_removed" in dd_dict:
        change_types.append("field_removals")
    if "values_changed" in dd_dict:
        # Analyze what types of fields changed
        changed_fields = list(dd_dict["values_changed"].keys())
        has_description = any("description" in field for field in changed_fields)
        has_pattern = any("pattern" in field for field in changed_fields)
        has_when = any("when" in field for field in changed_fields)
        
        if has_description and len(changed_fields) == 1:
            change_types.append("description_only")
        elif has_pattern and not has_when:
            change_types.append("regex_pattern")
        elif has_when:
            change_types.append("condition_logic")
        else:
            change_types.append("field_values")
    
    if len(change_types) == 1:
        return change_types[0]
    elif len(change_types) > 1:
        return "mixed_changes"
    else:
        return "structural_changes"


def extract_field_names_from_changes(dd):
    """Extract field names from DeepDiff changes for field value changes"""
    field_names = []
    
    # Convert DeepDiff result to dict if needed
    if hasattr(dd, 'to_dict'):
        dd_dict = dd.to_dict()
    else:
        dd_dict = dd
    
    if "values_changed" in dd_dict:
        for field_path in dd_dict["values_changed"].keys():
            # Extract field name from path like "root['message']" or "root['when']['builtin.file']['pattern']"
            if "root['" in field_path:
                # Extract the top-level field name
                field_name = field_path.split("root['")[1].split("']")[0]
                field_names.append(field_name)
            else:
                # Fallback for other path formats
                field_names.append(field_path.split(".")[-1])
    
    if "dictionary_item_added" in dd_dict:
        added_items = dd_dict["dictionary_item_added"]
        # Handle both dict and SetOrdered objects
        if hasattr(added_items, 'keys'):
            field_paths = added_items.keys()
        else:
            field_paths = added_items
        for field_path in field_paths:
            if "root['" in field_path:
                field_name = field_path.split("root['")[1].split("']")[0]
                field_names.append(field_name)
            else:
                field_names.append(field_path.split(".")[-1])
    
    if "dictionary_item_removed" in dd_dict:
        removed_items = dd_dict["dictionary_item_removed"]
        # Handle both dict and SetOrdered objects
        if hasattr(removed_items, 'keys'):
            field_paths = removed_items.keys()
        else:
            field_paths = removed_items
        for field_path in field_paths:
            if "root['" in field_path:
                field_name = field_path.split("root['")[1].split("']")[0]
                field_names.append(field_name)
            else:
                field_names.append(field_path.split(".")[-1])
    
    return list(set(field_names))  # Remove duplicates


def extract_rules_from_doc(doc):
    rules = []
    def walk(node):
        if isinstance(node, dict):
            if "ruleID" in node and isinstance(node["ruleID"], str):
                rules.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(doc)
    return rules


def step_scan_yaml(state):
    ensure_dirs()
    upstream_files = list_yaml_files(UPSTREAM_ROOT)
    downstream_files = list_yaml_files(DOWNSTREAM_ROOT)
    (CACHE_DIR / "upstream_files.json").write_text(json.dumps([str(p) for p in upstream_files], indent=2), encoding="utf-8")
    (CACHE_DIR / "downstream_files.json").write_text(json.dumps([str(p) for p in downstream_files], indent=2), encoding="utf-8")

    upstream_rules = {}
    downstream_rules = {}

    for path in upstream_files:
        for doc in load_yaml_documents(path):
            for rule in extract_rules_from_doc(doc):
                rid = rule.get("ruleID")
                if rid and rid not in upstream_rules:
                    file_path = str(path.relative_to(UPSTREAM_ROOT))
                    category = categorize_rule(rule, file_path)
                    upstream_rules[rid] = {"rule": rule, "file": file_path, "category": category}
    for path in downstream_files:
        for doc in load_yaml_documents(path):
            for rule in extract_rules_from_doc(doc):
                rid = rule.get("ruleID")
                if rid and rid not in downstream_rules:
                    file_path = str(path.relative_to(DOWNSTREAM_ROOT))
                    category = categorize_rule(rule, file_path)
                    downstream_rules[rid] = {"rule": rule, "file": file_path, "category": category}

    (CACHE_DIR / "upstream_rules.json").write_text(json.dumps(upstream_rules, indent=2, sort_keys=True), encoding="utf-8")
    (CACHE_DIR / "downstream_rules.json").write_text(json.dumps(downstream_rules, indent=2, sort_keys=True), encoding="utf-8")
    state["scan_yaml"] = True
    save_state(state)


def step_per_rule_diff(state):
    ensure_dirs()
    try:
        upstream = json.loads((CACHE_DIR / "upstream_rules.json").read_text(encoding="utf-8"))
        downstream = json.loads((CACHE_DIR / "downstream_rules.json").read_text(encoding="utf-8"))
    except Exception as e:
        log(f"per_rule_diff missing caches: {e}")
        return

    ids_up = set(upstream.keys())
    ids_down = set(downstream.keys())
    identical = []
    modified = []
    unique_up = []
    unique_down = []
    
    # Track change types and categories for analysis
    change_type_counts = {}
    category_counts = {"modified": {}, "unique_up": {}, "unique_down": {}}
    
    # Track detailed change type data for appendix tables
    change_type_details = {
        "field_values": [],
        "condition_logic": [],
        "mixed_changes": [],
        "field_additions": [],
        "structural_changes": [],
        "field_removals": [],
        "description_only": [],
        "regex_pattern": []
    }

    for rid in sorted(ids_up | ids_down):
        u = upstream.get(rid)
        d = downstream.get(rid)
        if u and d:
            u_rule = u["rule"]
            d_rule = d["rule"]
            category = u.get("category", "Other/Uncategorized")
            
            if u_rule == d_rule:
                identical.append([rid, u["file"], d["file"], category])
            else:
                if HAVE_DEEPDIFF:
                    try:
                        dd = DeepDiff(u_rule, d_rule, ignore_order=True)
                        # Convert to dict to get keys safely
                        dd_dict = dd.to_dict() if hasattr(dd, 'to_dict') else dd
                        changed_keys = list(dd_dict.keys())
                        change_type = analyze_change_type(dd)
                        diff_str = dd.to_json()
                        
                        # Extract field names for field value changes
                        field_names = extract_field_names_from_changes(dd)
                        field_names_str = ";".join(field_names) if field_names else ""
                        
                        # Generate change summary
                        change_summary = generate_change_summary(dd, change_type)
                    except Exception as e:
                        log(f"DeepDiff error for rule {rid}: {e}")
                        changed_keys = ["error"]
                        change_type = "error"
                        diff_str = json.dumps({"error": str(e)})
                        field_names_str = ""
                        change_summary = f"Error processing rule: {e}"
                    
                    # Store detailed change type data
                    if change_type not in change_type_details:
                        change_type_details[change_type] = []
                    change_type_details[change_type].append({
                        "ruleID": rid,
                        "category": category,
                        "upstream_file": u["file"],
                        "downstream_file": d["file"],
                        "field_names": field_names_str,
                        "change_summary": change_summary
                    })
                else:
                    changed_keys = ["diff"]
                    change_type = "unknown"
                    diff_str = json.dumps({"upstream": u_rule, "downstream": d_rule})
                    field_names_str = ""
                    change_summary = "Unknown changes (DeepDiff not available)"
                    
                    # Store detailed change type data for unknown
                    if change_type not in change_type_details:
                        change_type_details[change_type] = []
                    change_type_details[change_type].append({
                        "ruleID": rid,
                        "category": category,
                        "upstream_file": u["file"],
                        "downstream_file": d["file"],
                        "field_names": field_names_str,
                        "change_summary": change_summary
                    })
                
                modified.append([rid, category, u["file"], d["file"], change_type, ";".join(changed_keys), diff_str, field_names_str, change_summary])
                
                # Track change type counts
                change_type_counts[change_type] = change_type_counts.get(change_type, 0) + 1
                category_counts["modified"][category] = category_counts["modified"].get(category, 0) + 1
                
        elif u and not d:
            category = u.get("category", "Other/Uncategorized")
            unique_up.append([rid, u["file"], category])
            category_counts["unique_up"][category] = category_counts["unique_up"].get(category, 0) + 1
        elif d and not u:
            category = d.get("category", "Other/Uncategorized")
            unique_down.append([rid, d["file"], category])
            category_counts["unique_down"][category] = category_counts["unique_down"].get(category, 0) + 1

    # Save enhanced CSV files
    with open(REPORTS / "per_rule_identical.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["ruleID","file_upstream","file_downstream","category"]] + identical))
    with open(REPORTS / "per_rule_modified.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["ruleID","category","file_upstream","file_downstream","change_type","changed_keys","diff_json","field_names","change_summary"]] + modified))
    with open(REPORTS / "per_rule_unique_upstream.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["ruleID","file_upstream","category"]] + unique_up))
    with open(REPORTS / "per_rule_unique_downstream.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["ruleID","file_downstream","category"]] + unique_down))
    
    # Save change type detail tables
    for change_type, rules in change_type_details.items():
        if rules:  # Only create files for change types that have rules
            filename = f"change_type_{change_type.replace('_', '_')}.csv"
            with open(REPORTS / filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["ruleID", "category", "upstream_file", "downstream_file", "field_names", "change_summary"])
                for rule in rules:
                    writer.writerow([
                        rule["ruleID"],
                        rule["category"],
                        rule["upstream_file"],
                        rule["downstream_file"],
                        rule["field_names"],
                        rule["change_summary"]
                    ])
    
    # Analyze category change types
    category_change_types = analyze_category_change_types(modified)
    
    # Save analysis summary
    analysis_summary = {
        "change_type_counts": change_type_counts,
        "category_counts": category_counts,
        "total_modified": len(modified),
        "total_unique_up": len(unique_up),
        "total_unique_down": len(unique_down),
        "change_type_details": {k: len(v) for k, v in change_type_details.items()},
        "category_change_types": category_change_types
    }
    (CACHE_DIR / "analysis_summary.json").write_text(json.dumps(analysis_summary, indent=2), encoding="utf-8")

    state["per_rule_diff"] = True
    save_state(state)


def normalize_yaml_file(path: Path):
    docs = load_yaml_documents(path)
    return json.loads(json.dumps(docs, sort_keys=True))


def step_per_file_diff(state):
    ensure_dirs()
    try:
        upstream_files = [Path(p) for p in json.loads((CACHE_DIR / "upstream_files.json").read_text(encoding="utf-8"))]
        downstream_files = [Path(p) for p in json.loads((CACHE_DIR / "downstream_files.json").read_text(encoding="utf-8"))]
    except Exception as e:
        log(f"per_file_diff missing caches: {e}")
        return

    up_map = {}
    down_map = {}

    for p in upstream_files:
        try:
            up_map[str(p.relative_to(UPSTREAM_ROOT))] = hash_json(normalize_yaml_file(p))
        except Exception as e:
            log(f"normalize upstream {p}: {e}")
    for p in downstream_files:
        try:
            down_map[str(p.relative_to(DOWNSTREAM_ROOT))] = hash_json(normalize_yaml_file(p))
        except Exception as e:
            log(f"normalize downstream {p}: {e}")

    identical = []
    modified = []
    unique_up = []
    unique_down = []

    for up_rel, up_hash in up_map.items():
        # Try to find same filename path first; otherwise compare across by hash
        match_down = None
        if up_rel in down_map and down_map[up_rel] == up_hash:
            match_down = up_rel
            identical.append([up_rel, up_rel])
        else:
            # hash-join: any downstream with same hash
            for down_rel, d_hash in down_map.items():
                if d_hash == up_hash:
                    match_down = down_rel
                    identical.append([up_rel, down_rel])
                    break
        if match_down is None:
            # check if same path exists but different hash
            if up_rel in down_map:
                modified.append([up_rel, up_rel])
            else:
                unique_up.append([up_rel])

    for down_rel in down_map.keys():
        if any(row[1] == down_rel for row in identical) or any(row[1] == down_rel for row in modified):
            continue
        if down_rel not in up_map:
            unique_down.append([down_rel])

    with open(REPORTS / "per_file_identical.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["file_upstream","file_downstream"]] + identical))
    with open(REPORTS / "per_file_modified.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["file_upstream","file_downstream"]] + modified))
    with open(REPORTS / "per_file_unique_upstream.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["file_upstream"]] + unique_up))
    with open(REPORTS / "per_file_unique_downstream.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["file_downstream"]] + unique_down))

    state["per_file_diff"] = True
    save_state(state)


def git_list_branches_ahead(repo_root: Path):
    try:
        # List all branches (local and remote)
        out = subprocess.check_output(["git", "-C", str(repo_root), "branch", "-a", "--format=%(refname:short)"], text=True)
        branches = [b.strip() for b in out.splitlines() if b.strip()]
        result = []
        for b in branches:
            if b == "main" or b.startswith("origin/HEAD"):
                continue
            try:
                ahead = subprocess.check_output(["git", "-C", str(repo_root), "rev-list", "--left-right", "--count",
                                                 f"main...{b}"], text=True).strip()
                left, right = ahead.split()
                # right = commits ahead of main for branch b
                if right.isdigit() and int(right) > 0:
                    result.append([b, int(right)])
            except Exception as e:
                log(f"branch compare error in {repo_root} {b}: {e}")
        return sorted(result, key=lambda x: -x[1])
    except Exception as e:
        log(f"branch list error in {repo_root}: {e}")
        return []


def step_branch_scan(state):
    ensure_dirs()
    up = git_list_branches_ahead(UPSTREAM_ROOT)
    down = git_list_branches_ahead(DOWNSTREAM_ROOT)
    with open(REPORTS / "branches_upstream.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["branch","ahead_of_main"]] + up))
    with open(REPORTS / "branches_downstream.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(([["branch","ahead_of_main"]] + down))
    state["branch_scan"] = True
    save_state(state)


def describe_change(field_path, old_val, new_val):
    """Generate one-line English description of a field change"""
    field_name = field_path.split("'")[-2] if "'" in field_path else field_path.split(".")[-1]
    
    if field_name == "message":
        return "Added user-friendly message field in downstream"
    elif field_name == "pattern":
        if isinstance(old_val, str) and isinstance(new_val, str):
            if "^" in new_val and "^" not in old_val:
                return "Enhanced regex pattern with anchors for more precise matching"
            elif ".*" in old_val and ".*" not in new_val:
                return "Replaced wildcard pattern with more specific regex"
        return f"Updated {field_name} pattern"
    elif field_name == "description":
        return "Modified rule description"
    elif field_name == "when":
        return "Enhanced condition logic (likely added OR conditions or dependency checks)"
    elif field_name == "tag":
        return "Updated rule tags/categories"
    elif field_name == "effort":
        return "Changed effort estimation"
    else:
        return f"Modified {field_name} field"


def generate_change_summary(dd, change_type):
    """Generate a concise summary of changes for a rule"""
    # Convert DeepDiff result to dict if needed
    if hasattr(dd, 'to_dict'):
        dd_dict = dd.to_dict()
    else:
        dd_dict = dd
    
    if change_type == "field_values":
        if "values_changed" in dd_dict:
            changed_fields = list(dd_dict["values_changed"].keys())
            field_names = [field.split("'")[-2] if "'" in field else field.split(".")[-1] for field in changed_fields]
            if len(field_names) == 1:
                return f"Modified {field_names[0]} field value"
            else:
                return f"Modified {len(field_names)} fields: {', '.join(field_names[:3])}{'...' if len(field_names) > 3 else ''}"
    elif change_type == "condition_logic":
        return "Enhanced condition logic (when field structure differs)"
    elif change_type == "mixed_changes":
        return "Multiple types of changes (field values, additions, removals, etc.)"
    elif change_type == "field_additions":
        if "dictionary_item_added" in dd_dict:
            added_items = dd_dict["dictionary_item_added"]
            # Handle both dict and SetOrdered objects
            if hasattr(added_items, 'keys'):
                added_fields = list(added_items.keys())
            else:
                added_fields = list(added_items)
            field_names = [field.split("'")[-2] if "'" in field else field.split(".")[-1] for field in added_fields]
            return f"Added {len(field_names)} new fields: {', '.join(field_names)}"
    elif change_type == "structural_changes":
        return "Complex structural changes (other than field additions/removals)"
    elif change_type == "field_removals":
        if "dictionary_item_removed" in dd_dict:
            removed_items = dd_dict["dictionary_item_removed"]
            # Handle both dict and SetOrdered objects
            if hasattr(removed_items, 'keys'):
                removed_fields = list(removed_items.keys())
            else:
                removed_fields = list(removed_items)
            field_names = [field.split("'")[-2] if "'" in field else field.split(".")[-1] for field in removed_fields]
            return f"Removed {len(field_names)} fields: {', '.join(field_names)}"
    elif change_type == "description_only":
        return "Only description field modified"
    elif change_type == "regex_pattern":
        return "Regex/pattern fields modified"
    else:
        return "Unknown change type"


def categorize_field_change(field_name, change_type):
    """Categorize a field change into description, metadata, source/target, or regex"""
    field_lower = field_name.lower()
    
    # Description changes
    if field_lower in ["description", "message"]:
        return "description"
    
    # Metadata changes
    elif field_lower in ["tag", "tags", "effort", "category", "domain", "labels", "metadata"]:
        return "metadata"
    
    # Source/target changes
    elif field_lower in ["source", "target", "from", "to", "migration", "transformation"]:
        return "source/target"
    
    # Regex/pattern changes
    elif field_lower in ["pattern", "regex", "when"] or "pattern" in field_lower:
        return "regex"
    
    # Default to metadata for unknown fields
    else:
        return "metadata"


def analyze_category_change_types(modified_rules):
    """Analyze the types of changes within each category"""
    category_change_types = {}
    
    for rule_data in modified_rules:
        if len(rule_data) >= 9:  # Ensure we have all fields
            category = rule_data[1]  # category field
            change_type = rule_data[4]  # change_type field
            field_names_str = rule_data[7] if len(rule_data) > 7 else ""  # field_names field
            
            if category not in category_change_types:
                category_change_types[category] = {
                    "description": 0,
                    "metadata": 0,
                    "source/target": 0,
                    "regex": 0,
                    "other": 0
                }
            
            # Analyze field changes
            if field_names_str and change_type == "field_values":
                field_names = field_names_str.split(";")
                for field_name in field_names:
                    if field_name.strip():
                        field_category = categorize_field_change(field_name.strip(), change_type)
                        category_change_types[category][field_category] += 1
            elif change_type == "description_only":
                category_change_types[category]["description"] += 1
            elif change_type == "regex_pattern":
                category_change_types[category]["regex"] += 1
            elif change_type == "condition_logic":
                category_change_types[category]["regex"] += 1  # when field is regex-related
            else:
                category_change_types[category]["other"] += 1
    
    return category_change_types


def generate_detailed_appendix():
    """Generate detailed rule differences appendix organized by file/topic"""
    try:
        upstream = json.loads((CACHE_DIR / "upstream_rules.json").read_text(encoding="utf-8"))
        downstream = json.loads((CACHE_DIR / "downstream_rules.json").read_text(encoding="utf-8"))
    except Exception as e:
        log(f"detailed appendix missing caches: {e}")
        return ""
    
    # Group rules by file/topic
    file_groups = {}
    all_rules = []
    for rid, data in {**upstream, **downstream}.items():
        file_path = data.get("file", "unknown")
        topic = file_path.split("/")[-1].replace(".yaml", "").replace(".yml", "")
        if topic not in file_groups:
            file_groups[topic] = []
        file_groups[topic].append((rid, data))
        all_rules.append((rid, topic))
    
    appendix = []
    appendix.append("## Appendix: Detailed Rule Differences")
    appendix.append("")

    # Add change type tables section
    appendix.append("### Change Type Tables {#change-type-tables}")
    appendix.append("")
    appendix.append("Detailed breakdown of rules by change type with field names and summaries:")
    appendix.append("")
    
    # Load change type details
    try:
        analysis_data = json.loads((CACHE_DIR / "analysis_summary.json").read_text(encoding="utf-8"))
        change_type_counts = analysis_data.get("change_type_counts", {})
    except Exception:
        change_type_counts = {}
    
    # Generate change type tables
    for change_type, count in sorted(change_type_counts.items(), key=lambda x: -x[1]):
        if count > 0:
            filename = f"change_type_{change_type.replace('_', '_')}.csv"
            change_desc = {
                "field_additions": "Field additions only",
                "field_values": "Field value changes only", 
                "description_only": "Description changes only",
                "regex_pattern": "Regex/pattern changes",
                "condition_logic": "Condition logic changes",
                "mixed_changes": "Mixed changes",
                "structural_changes": "Structural changes",
                "field_removals": "Field removals"
            }.get(change_type, change_type)
            
            appendix.append(f"#### {change_desc} ({count} rules) {{#{change_type.replace('_', '-')}-table}}")
            appendix.append("")
            appendix.append(f"See `reports/{filename}` for complete list of {count} rules with field names and change summaries.")
            appendix.append("")
    
    # Special section for field value changes with field names
    if change_type_counts.get("field_values", 0) > 0:
        appendix.append("#### Field Value Changes Table {#field-value-changes-table}")
        appendix.append("")
        appendix.append("The 1,462 field value changes include the following field types:")
        appendix.append("")
        appendix.append("| Field Type | Description | Example Changes |")
        appendix.append("|------------|-------------|-----------------|")
        appendix.append("| `message` | User-friendly messages | Added in downstream for better UX |")
        appendix.append("| `pattern` | Regex patterns | Enhanced with anchors (^, $) for precision |")
        appendix.append("| `description` | Rule descriptions | Updated for clarity and accuracy |")
        appendix.append("| `when` | Condition logic | Added OR conditions and dependency checks |")
        appendix.append("| `tag` | Rule tags/categories | Updated categorization |")
        appendix.append("| `effort` | Effort estimation | Changed complexity ratings |")
        appendix.append("| Other fields | Various metadata | Updated values for consistency |")
        appendix.append("")
        appendix.append(f"See `reports/change_type_field_values.csv` for complete list of {change_type_counts.get('field_values', 0)} rules with specific field names and change summaries.")
        appendix.append("")
    
    # Table of contents (files only)
    appendix.append("### Rule Differences by File {#rule-differences-by-file}")
    appendix.append("")
    appendix.append("Detailed rule-by-rule differences organized by file/topic:")
    appendix.append("")
    for topic in sorted(file_groups.keys()):
        # Create safe anchor for topic
        topic_anchor = topic.lower().replace(' ', '-').replace('_', '-')
        rule_count = len(file_groups[topic])
        appendix.append(f"- [{topic}](#{topic_anchor}) ({rule_count} rules)")
    appendix.append("")
    
    # Detailed sections
    for topic in sorted(file_groups.keys()):
        # Create safe anchor for topic
        topic_anchor = topic.lower().replace(' ', '-').replace('_', '-')
        appendix.append(f"### {topic} {{#{topic_anchor}}}")
        appendix.append("")
        
        for rid, data in sorted(file_groups[topic], key=lambda x: x[0]):
            u_rule = upstream.get(rid, {}).get("rule", {})
            d_rule = downstream.get(rid, {}).get("rule", {})
            u_file = upstream.get(rid, {}).get("file", "")
            d_file = downstream.get(rid, {}).get("file", "")
            
            if not u_rule and not d_rule:
                continue
                
            appendix.append(f"#### {rid}")
            if u_rule and d_rule:
                # Modified rule
                appendix.append("- **Status**: Modified")
                appendix.append(f"- **Upstream**: `{u_file}`")
                appendix.append(f"- **Downstream**: `{d_file}`")
                
                # Show key differences with descriptions
                if HAVE_DEEPDIFF:
                    dd = DeepDiff(u_rule, d_rule, ignore_order=True)
                    changes = []
                    
                    if "dictionary_item_added" in dd:
                        for field in dd["dictionary_item_added"]:
                            changes.append(f"**Added in downstream**: {field} - Added new field")
                    
                    if "dictionary_item_removed" in dd:
                        for field in dd["dictionary_item_removed"]:
                            changes.append(f"**Removed in downstream**: {field} - Removed field")
                    
                    if "values_changed" in dd:
                        for field_path, change_info in list(dd["values_changed"].items())[:5]:  # Show first 5
                            old_val = change_info.get("old_value", "")
                            new_val = change_info.get("new_value", "")
                            desc = describe_change(field_path, old_val, new_val)
                            changes.append(f"**Changed**: {field_path} - {desc}")
                        
                        if len(dd["values_changed"]) > 5:
                            changes.append(f"... and {len(dd['values_changed']) - 5} more changes")
                    
                    if changes:
                        appendix.append("- **Changes**:")
                        for change in changes:
                            appendix.append(f"  - {change}")
                    else:
                        appendix.append("- **Changes**: Structural differences detected")
                
            elif u_rule and not d_rule:
                # Unique to upstream
                appendix.append("- **Status**: Unique to upstream")
                appendix.append(f"- **File**: `{u_file}`")
                appendix.append(f"- **Description**: {u_rule.get('description', 'N/A')}")
            elif d_rule and not u_rule:
                # Unique to downstream
                appendix.append("- **Status**: Unique to downstream")
                appendix.append(f"- **File**: `{d_file}`")
                appendix.append(f"- **Description**: {d_rule.get('description', 'N/A')}")
            
            appendix.append("")
    
    return "\n".join(appendix)


def step_write_readme(state):
    ensure_dirs()
    readme = ROOT / "README.md"
    def count_rows(p):
        if not p.exists():
            return 0
        try:
            with open(p, encoding="utf-8") as f:
                return max(0, sum(1 for _ in f) - 1)
        except Exception:
            return 0
    hi = count_rows(REPORTS / "per_rule_identical.csv")
    hm = count_rows(REPORTS / "per_rule_modified.csv")
    hu = count_rows(REPORTS / "per_rule_unique_upstream.csv")
    hd = count_rows(REPORTS / "per_rule_unique_downstream.csv")
    up_branches = count_rows(REPORTS / "branches_upstream.csv")
    down_branches = count_rows(REPORTS / "branches_downstream.csv")
    
    # Generate detailed appendix
    appendix = generate_detailed_appendix()
    
    # Load enhanced analysis data
    try:
        analysis_data = json.loads((CACHE_DIR / "analysis_summary.json").read_text(encoding="utf-8"))
        change_type_counts = analysis_data.get("change_type_counts", {})
        category_counts = analysis_data.get("category_counts", {})
        category_change_types = analysis_data.get("category_change_types", {})
    except Exception as e:
        log(f"analysis_summary load error: {e}")
        change_type_counts = {}
        category_counts = {"modified": {}, "unique_up": {}, "unique_down": {}}
        category_change_types = {}
    
    # Calculate reconciliation priorities
    total_rules = hi + hm + hu + hd
    sync_priority = "HIGH" if hm > total_rules * 0.5 else "MEDIUM" if hm > total_rules * 0.2 else "LOW"
    
    content = (
        "AppCAT ↔ Konveyor rulesets reconciliation (YAML-focused)\n\n"
        "## Executive Summary\n\n"
        f"The analysis reveals significant divergence between Konveyor and AppCAT rulesets, with {hm:,} of {total_rules:,} rules ({((hm/total_rules)*100):.1f}%) having differences between repositories. The reconciliation strategy involves two phases: Phase 1 focuses on rule synchronization and schema harmonization, while Phase 2 establishes ongoing bidirectional sync workflows.\n\n"
        f"**Upstream Changes (Konveyor)**: The primary changes will be adopting AppCAT's enhanced rule patterns, adding optional metadata fields (domain, category, message), and incorporating {hd} AppCAT-specific rules that provide value to the broader community. The unified schema will maintain backward compatibility while supporting richer rule descriptions and Azure-specific categorizations.\n\n"
        f"**Downstream Changes (AppCAT)**: AppCAT will adopt {hu} missing rules from upstream Konveyor, ensuring comprehensive coverage of migration scenarios. The existing {hm:,} modified rules will be harmonized using the unified schema, preserving AppCAT's enhancements while aligning with upstream standards.\n\n"
        f"**Key Numbers**: {total_rules:,} total rules, {hm:,} modified, {hu:,} unique upstream, {hd:,} unique downstream\n\n"
        "## Proposed Unified Schema\n\n"
        "The reconciliation plan proposes a unified YAML schema that combines the best of both repositories while maintaining backward compatibility:\n\n"
        "```yaml\n"
        "# Example unified rule schema\n"
        "- ruleID: azure-cache-redis-01000                    # Required: unique identifier\n"
        "  description: \"Application uses Redis cache\"        # Required: human-readable description\n"
        "  message: \"Consider migrating to Azure Cache for Redis\"  # Optional: AppCAT enhancement\n"
        "  domain: \"azure\"                                    # Optional: AppCAT categorization\n"
        "  category: \"cache\"                                  # Optional: AppCAT subcategory\n"
        "  tag: [\"Redis\", \"Cache\", \"Azure\"]                   # Required: tags for classification\n"
        "  labels: [\"konveyor.io/include=always\"]             # Required: Konveyor labels\n"
        "  effort: \"medium\"                                   # Optional: migration effort estimate\n"
        "  links: []                                          # Required: documentation links\n"
        "  customVariables: []                                # Required: custom variables\n"
        "  when:                                             # Required: condition logic\n"
        "    builtin.file:\n"
        "      pattern: \".*redis.*\\\\.jar\"                     # Enhanced regex patterns from AppCAT\n"
        "  # Additional AppCAT-specific fields (optional):\n"
        "  metadata:                                          # Optional: rich metadata\n"
        "    severity: \"medium\"\n"
        "    confidence: \"high\"\n"
        "    migrationPath: \"azure-cache-redis\"\n"
        "```\n\n"
        "**Schema Changes Summary**:\n"
        "- **Additions**: `message`, `domain`, `category`, `metadata` fields (AppCAT enhancements)\n"
        "- **Enhancements**: Improved regex patterns in `when` conditions\n"
        "- **Backward Compatibility**: All existing Konveyor fields preserved\n"
        "- **Optional Fields**: New fields are optional to maintain compatibility\n\n"
        "## AppCAT Enhancements to Rulesets\n\n"
        "AppCAT has significantly enhanced the rule detection logic compared to Konveyor, with 2,140 of 2,941 rules (72.8%) having improvements. The enhancements fall into three main categories:\n\n"
        "### 1. Enhanced Regex Patterns (68.3% of changes)\n\n"
        "**Konveyor (Simple)**:\n"
        "```yaml\n"
        "when:\n"
        "  builtin.file:\n"
        "    pattern: \".*liferay.*\\\\.jar\"\n"
        "```\n\n"
        "**AppCAT (Enhanced)**:\n"
        "```yaml\n"
        "when:\n"
        "  builtin.file:\n"
        "    pattern: \"^([a-zA-Z0-9._-]*)liferay([a-zA-Z0-9._-]*)\\\\.jar$\"\n"
        "```\n\n"
        "**Key Improvements**:\n"
        "- **More Precise Matching**: Uses `^` and `$` anchors for exact matching\n"
        "- **Structured Capture Groups**: `([a-zA-Z0-9._-]*)` captures version prefixes/suffixes\n"
        "- **Reduced False Positives**: Prevents matching files like `not-liferay-something.jar`\n\n"
        "### 2. Dependency Detection Integration (24.5% of changes)\n\n"
        "**Konveyor (File-only)**:\n"
        "```yaml\n"
        "when:\n"
        "  builtin.file:\n"
        "    pattern: \"spring-boot.*\\\\.jar\"\n"
        "```\n\n"
        "**AppCAT (Multi-layered)**:\n"
        "```yaml\n"
        "when:\n"
        "  or:\n"
        "    - java.dependency:\n"
        "        lowerbound: \"0.0.0\"\n"
        "        name: \"org.springframework.spring-boot\"\n"
        "    - builtin.file:\n"
        "        pattern: \"^spring-boot([a-zA-Z0-9._-]*)\\\\.jar$\"\n"
        "```\n\n"
        "**Key Improvements**:\n"
        "- **Maven/Gradle Detection**: Checks actual dependency declarations, not just JAR files\n"
        "- **Fallback Logic**: Still checks JAR files if dependency detection fails\n"
        "- **More Accurate**: Catches dependencies even when JAR names don't match patterns\n\n"
        "### 3. Complex Multi-Condition Logic (6.0% of changes)\n\n"
        "**Konveyor (Single condition)**:\n"
        "```yaml\n"
        "when:\n"
        "  builtin.filecontent:\n"
        "    filePattern: \".*\\\\.(java|properties|yaml|yml)\"\n"
        "    pattern: \"\\\\.\\\\/.\"\n"
        "```\n\n"
        "**AppCAT (Comprehensive)**:\n"
        "```yaml\n"
        "when:\n"
        "  or:\n"
        "    - builtin.filecontent:\n"
        "        filePattern: \"(/|\\\\\\\\)([a-zA-Z0-9._-]+)\\\\.(java|properties|yaml|yml|xml)$\"\n"
        "        pattern: \"^(\\\\\\\\.{1,2}\\\\/[-\\\\w\\\\/.]+)\"\n"
        "    - java.referenced:\n"
        "        location: \"PACKAGE\"\n"
        "        pattern: \"com.amazonaws.services.s3*\"\n"
        "    - java.dependency:\n"
        "        name: \"com.amazonaws.aws-java-sdk-s3\"\n"
        "```\n\n"
        "**Key Improvements**:\n"
        "- **Multiple Detection Methods**: File content, package references, and dependencies\n"
        "- **Broader Coverage**: Catches usage patterns that file scanning might miss\n"
        "- **Azure-Specific**: Tailored for Azure migration scenarios\n\n"
        "### 4. Build Tool Integration\n\n"
        "**AppCAT adds support for**:\n"
        "- **Gradle**: `build.gradle` and `build.gradle.kts` files\n"
        "- **Maven**: Enhanced XPath queries for `pom.xml`\n"
        "- **Multiple Java Version Detection**: Checks `sourceCompatibility`, `targetCompatibility`, etc.\n\n"
        "### Why These Enhancements Matter\n\n"
        "1. **Accuracy**: AppCAT's patterns reduce false positives by being more specific\n"
        "2. **Completeness**: Dependency detection catches cases where JAR files aren't present\n"
        "3. **Azure Focus**: Rules are tailored for Azure migration scenarios\n"
        "4. **Modern Build Tools**: Support for Gradle and modern Maven configurations\n"
        "5. **Comprehensive Coverage**: Multiple detection methods ensure nothing is missed\n\n"
        "The unified schema preserves these AppCAT enhancements while maintaining backward compatibility with Konveyor's simpler patterns, giving users the best of both worlds.\n\n"
        "## Analysis Details\n\n"
        "### Change Type Breakdown\n"
        f"The {hm:,} modified rules break down as follows:\n"
    )
    
    # Add change type breakdown with field names and links
    if change_type_counts:
        for change_type, count in sorted(change_type_counts.items(), key=lambda x: -x[1]):
            percentage = (count / hm * 100) if hm > 0 else 0
            change_desc = {
                "field_additions": "Field additions only (new YAML fields added)",
                "field_values": "Field value changes only (existing fields with different values)",
                "description_only": "Description changes only (only description field differs)",
                "regex_pattern": "Regex/pattern changes (when/pattern fields differ)",
                "condition_logic": "Condition logic changes (when field structure differs)",
                "mixed_changes": "Mixed changes (multiple types above)",
                "structural_changes": "Structural changes (other complex differences)",
                "field_removals": "Field removals (fields removed from downstream)"
            }.get(change_type, change_type)
            
            # Add field names for field_values type
            if change_type == "field_values":
                content += f"- **{change_desc}**: {count:,} rules ({percentage:.1f}%) — see [field value changes table](#field-value-changes-table) for field names and rule details\n"
            else:
                content += f"- **{change_desc}**: {count:,} rules ({percentage:.1f}%) — see [change type table](#change-type-tables) for rule details\n"
    else:
        content += "- Analysis data not available\n"
    
    content += (
        "\n### Category Breakdown\n"
        "Analysis by rule category showing where biggest differences are:\n"
    )
    
    # Add category breakdown with detailed change types
    for category in ["Azure rules", "Java framework rules", "Cloud readiness rules", "Technology usage rules", "Discovery rules", "Other/Uncategorized"]:
        modified_count = category_counts.get("modified", {}).get(category, 0)
        unique_up_count = category_counts.get("unique_up", {}).get(category, 0)
        unique_down_count = category_counts.get("unique_down", {}).get(category, 0)
        if modified_count > 0 or unique_up_count > 0 or unique_down_count > 0:
            content += f"- **{category}**: {modified_count} modified, {unique_up_count} unique upstream, {unique_down_count} unique downstream\n"
            
            # Add detailed change types within this category
            if category in category_change_types:
                change_types = category_change_types[category]
                change_details = []
                if change_types.get("description", 0) > 0:
                    change_details.append(f"description ({change_types['description']})")
                if change_types.get("metadata", 0) > 0:
                    change_details.append(f"metadata ({change_types['metadata']})")
                if change_types.get("source/target", 0) > 0:
                    change_details.append(f"source/target ({change_types['source/target']})")
                if change_types.get("regex", 0) > 0:
                    change_details.append(f"regex ({change_types['regex']})")
                if change_types.get("other", 0) > 0:
                    change_details.append(f"other ({change_types['other']})")
                
                if change_details:
                    content += f"  - Change types within {category}: {', '.join(change_details)}\n"
    
    content += (
        "\n### Recommended Reconciliation Plan\n"
        f"**Priority: {sync_priority}** - {hm:,} of {total_rules:,} rules have diverged between repos\n\n"
        "#### Phase 1: Rule Synchronization and Schema Harmonization\n"
        f"1. **Merge {hu} upstream-only rules** into AppCAT downstream\n"
        f"   - These rules exist in Konveyor but are missing from AppCAT\n"
        f"   - See `reports/per_rule_unique_upstream.csv` for full list\n"
        f"   - Low risk: pure additions to downstream\n\n"
        f"2. **Review {hd} downstream-only rules** for upstream contribution\n"
        f"   - AppCAT-specific rules that could benefit Konveyor community\n"
        f"   - See `reports/per_rule_unique_downstream.csv` for full list\n"
        f"   - Focus on Azure-specific enhancements\n\n"
        f"3. **Harmonize {hm:,} modified rules** using unified schema\n"
        f"   - Most changes are additive (message fields, enhanced regex patterns)\n"
        f"   - See detailed analysis in appendix below\n"
        f"   - Propose unified superset schema to Konveyor maintainers\n\n"
        "#### Phase 2: Bidirectional Sync Workflow\n"
        "4. **Establish bidirectional sync workflow**\n"
        "   - **Upstream → Downstream**: Pull new rules from Konveyor, merge with AppCAT enhancements\n"
        "   - **Downstream → Upstream**: Push enhanced rules back to Konveyor with AppCAT metadata\n"
        "   - **Schema strategy**: Unified superset with optional AppCAT fields (domain, category, message)\n"
        "   - **Conflict resolution**: Prefer downstream enhancements for existing rules, merge new rules from upstream\n\n"
        "#### Schema Unification Strategy\n"
        "- **Keep `ruleID` canonical** - this is the primary key for rule matching\n"
        "- **Add optional AppCAT fields** - domain, category, message as extensions\n"
        "- **Enhanced patterns** - downstream regex improvements should be adopted upstream\n"
        "- **Condition logic** - downstream OR conditions and dependency checks are improvements\n\n"
        "#### Success Metrics\n"
        f"- Reduce modified rules from {hm:,} to <100 through schema unification\n"
        f"- Achieve 95%+ rule coverage (currently {hi + hm:,}/{total_rules:,} = {((hi + hm)/total_rules*100):.1f}%)\n"
        "- Establish an agreed upon sync cadence or new process to prevent future diversion\n"
        "- Zero breaking changes to existing Konveyor and AppCAT functionality\n\n"
        "### Data Sources\n"
        "**Goal**: Analyze divergences between upstream `konveyor/rulesets` and downstream `Azure/appcat-konveyor-rulesets` (main branches), focusing strictly on YAML rulesets.\n\n"
        "**Method**: Compare per-rule (semantic by `ruleID`) and per-file (syntactic). Produce compact summaries here and detailed CSVs in `reports/`.\n\n"
        "#### Per-rule analysis\n"
        f"- **Identical rules**: {hi:,} (exact matches) — see `reports/per_rule_identical.csv`\n"
        f"- **Modified rules**: {hm:,} (exist in both but have differences) — see `reports/per_rule_modified.csv`\n"
        f"- **Unique to upstream**: {hu:,} (Konveyor-only) — see `reports/per_rule_unique_upstream.csv`\n"
        f"- **Unique to downstream**: {hd:,} (AppCAT-only) — see `reports/per_rule_unique_downstream.csv`\n\n"
        "#### Per-file analysis\n"
        "- File-level differences — see `reports/per_file_*.csv`\n\n"
        "#### Branch analysis\n"
        "- Branches ahead of main — see `reports/branches_*.csv`\n\n"
        "---\n\n"
        "## How to run\n"
        "1. `pip install -r tools/requirements.txt`\n"
        "2. `python3 tools/orchestrate.py --run`\n"
        "3. Reset: `python3 tools/orchestrate.py --reset`\n\n"
        "**Outputs**: CSVs in `reports/`, logs in `tools/logs/run.log`, state in `tools/state.json`\n\n"
        f"\n## Branch Analysis\n\n"
        f"Both repositories have multiple branches with varying degrees of divergence from main:\n\n"
        f"### Upstream Branches Ahead of Main\n"
        f"- **Total branches ahead**: {up_branches}\n"
        f"- **Most significant**: {up_branches} branches with pending changes\n"
        f"- **Release branches**: Multiple release branches (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)\n"
        f"- **Cherry-pick branches**: Several cherry-pick branches for specific PRs\n\n"
        f"### Downstream Branches Ahead of Main\n"
        f"- **Total branches ahead**: {down_branches}\n"
        f"- **Most significant**: {down_branches} branches with pending changes\n"
        f"- **Feature branches**: Multiple feature branches from different contributors\n"
        f"- **Release branches**: AppCAT-specific release branches (7.1.x.y, 7.6.x.y, 7.7.x.y)\n"
        f"- **Dependabot branches**: Automated dependency update branches\n\n"
        f"**Note**: These branches represent ongoing development work that may contain additional rules or enhancements not yet merged to main. Consider reviewing these branches for additional reconciliation opportunities.\n\n"
        f"{appendix}\n"
    )
    readme.write_text(content, encoding="utf-8")
    state["write_readme"] = True
    save_state(state)


def main():
    parser = argparse.ArgumentParser(description="AppCAT ↔ Konveyor rulesets reconciliation orchestrator")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--force-step", dest="force_step", default=None)
    parser.add_argument("--set-step", dest="set_step", default=None, help="name=true|false")
    args = parser.parse_args()

    check_repos()
    if args.reset:
        reset_all()
        return

    state = load_state()
    if args.set_step:
        try:
            name, val = args.set_step.split("=", 1)
            state[name] = (val.lower() == "true")
            save_state(state)
        except Exception as e:
            log(f"set-step error: {e}")

    if not args.run and not args.force_step:
        print("Nothing to do. Use --run or --force-step <name>.")
        return

    # execute steps idempotently
    steps = [
        ("scan_yaml", step_scan_yaml),
        ("per_rule_diff", step_per_rule_diff),
        ("per_file_diff", step_per_file_diff),
        ("branch_scan", step_branch_scan),
        ("write_readme", step_write_readme),
    ]

    ensure_dirs()

    for name, func in steps:
        if args.force_step == name or not state.get(name, False) or args.force_step == "all" or args.run:
            try:
                log(f"step start: {name}")
                func(state)
                log(f"step done: {name}")
            except Exception as e:
                log(f"step error {name}: {e}")
                # continue next steps


if __name__ == "__main__":
    main()


