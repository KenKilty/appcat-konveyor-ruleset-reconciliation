You are an engineer analyzing two diverged repositories to produce a minimal, repeatable reconciliation plan and a compact report in `README.md`.

System role
- Be succinct and specific. Avoid business speak.
- Goal: Compare upstream Konveyor rulesets (`rulesets/`) and downstream AppCAT rulesets (`appcat-konveyor-rulesets/`) focused strictly on YAML rulesets. Produce a plan and compact analysis in `README.md` and CSVs in `reports/`.
- Priorities: correctness, idempotency, reproducibility, compact reporting.

Scope and constraints
- Analyze `main` branches of both repos. Also list branches that are fully ahead of main in either repo (no content diffing on those branches, just list).
- Only consider YAML files; ignore non-YAML. Recurse all subdirectories. Do not rely on repo structure or filenames for comparison.
- Compare on two axes:
  1) Per-rule semantic comparison (match by `ruleID`, even if filenames diverged; multiple rules per file allowed).
  2) Per-file syntactic comparison (normalized content hash/diff).
- `ruleID` uniquely identifies a rule (e.g., `azure-cache-redis-01000`).
- Favor a unified superset schema if changes are additive; document proposal briefly with a compact YAML sample showing the unified schema between executive summary and analysis details.
- Use Python 3 and `yq` if helpful. Small deps allowed. Target macOS/Linux/WSL2.
- Idempotent process: cache normalized artifacts; track step completion via a simple state file; provide a reset option to wipe state, logs, reports, and regenerate `README.md`.
- Logging: do not fail the whole run on one error; log errors to a unified log file under `tools/logs/`. Ensure logs are ignored by git.
- **DeepDiff error handling**: Handle complex YAML structures that may cause SetOrdered object errors. Wrap DeepDiff operations in try-catch blocks and log specific rule errors without stopping the analysis. Specifically handle SetOrdered objects returned by DeepDiff.to_dict() by checking for .keys() method before calling it, and iterate directly over SetOrdered objects when they don't have .keys(). Convert DeepDiff results to dictionaries using dd.to_dict() when available before accessing keys or iterating.
- Output CSVs under `reports/` for easy post-processing.

Repository layout in this workspace
- Upstream: `rulesets/`
- Downstream: `appcat-konveyor-rulesets/`

What to build
- `tools/orchestrate.py` (single entrypoint)
  - Steps: scan_yaml, per_rule_diff, per_file_diff, branch_scan, write_readme
  - CLI: `--run`, `--reset`, `--force-step <name>`, `--set-step <name>=true|false`
  - Idempotent via `tools/state.json`. Reset wipes `reports/`, `tools/logs/`, caches, state, and rewrites `README.md`.
  - Unified log: `tools/logs/run.log`.
  - Caches allowed (e.g., normalized JSON per YAML path).
- `reports/` directory for CSV outputs.
- `tools/requirements.txt` listing Python deps (PyYAML, deepdiff, jsonschema optional).
- `.gitignore` entries to exclude logs and transient state/cache.
- **Dependency detection and installation**: The orchestrator must detect missing dependencies and provide clear error messages. DeepDiff is critical for change type analysis and must be installed. Check for HAVE_DEEPDIFF flag and install dependencies automatically if missing.

Virtual environment
- Prefer a local venv to scope dependencies:
  - With pyenv-virtualenv (if available):
    - `pyenv install -s 3.11.9 && pyenv virtualenv -f 3.11.9 appcat-konveyor-rs && pyenv local appcat-konveyor-rs`
  - Built-in venv fallback:
    - `python3 -m venv .venv && source .venv/bin/activate`
  - Then: `pip install -r tools/requirements.txt`

Comparison details
- YAML discovery: recurse both repos; collect `**/*.{yaml,yml}`.
- Rule extraction (per-rule): parse YAML (support multi-doc). Extract any object with a `ruleID` key at any depth; if nested, capture the immediate mapping representing the rule. Keep source file path reference.
- Rule categorization: classify rules into broad categories based on file paths and rule content:
  - Azure rules (azure/*, azure-*)
  - Java framework rules (spring-*, hibernate, quarkus, etc.)
  - Cloud readiness rules (cloud-readiness/*, embedded-cache, etc.)
  - Technology usage rules (technology-usage/*, 3rd-party, etc.)
  - Discovery rules (00-discovery/*)
  - Other/Uncategorized
- Per-rule compare: build maps `upstream: ruleID -> ruleObject`, `downstream: ruleID -> ruleObject`. Use deep comparison to classify:
  - identical
  - modified (with detailed change type analysis)
  - unique_upstream
  - unique_downstream
- Change type analysis for modified rules:
  - Field additions only (new YAML fields added)
  - Field value changes only (existing fields with different values) - **with field names extracted**
  - Description changes only (only description field differs)
  - Regex/pattern changes (when/pattern fields differ)
  - Condition logic changes (when field structure differs)
  - Mixed changes (multiple types above)
  - Structural changes (other complex differences)
- Output CSVs with columns: ruleID, category, file_upstream, file_downstream, change_type, changed_keys, diff_json, field_names, change_summary.
- Generate separate change type CSV files for each change type with detailed field analysis.
- **Field name extraction**: Extract specific field names from DeepDiff results for field value changes (e.g., message, pattern, description, when, tag, effort). Handle SetOrdered objects by checking for .keys() method and converting to dict when possible.
- **Change type CSV generation**: Create individual CSV files for each change type (change_type_field_values.csv, change_type_condition_logic.csv, etc.) with rule details and field names. Include error handling for rules that cause DeepDiff failures.
- Per-file compare: normalize by converting to JSON with sorted keys and stripping trivial whitespace. Classify files by content hash:
  - identical
  - modified (store a short diff summary)
  - unique_upstream
  - unique_downstream

Branch scan (listing only)
- For each repo, list local and remote branches that are ahead of `main` (no diffing). Include branch name and ahead count.
- Use `git branch -a` to include remote branches, filter out `origin/HEAD` branches.
- Use local git repos only; do not rely on remotes being configured.

README.md content (organized from general to specific)
- **Executive Summary** (high-level overview only)
  - Summary of divergence extent and reconciliation approach
  - Two-phase strategy description
  - Upstream changes summary (recommendation for Konveyor to consider adopting AppCAT enhancements)
  - Downstream changes summary (what will change in AppCAT)
  - Key numbers: X total rules, Y modified, Z unique upstream, W unique downstream

- **Proposed Unified Schema** (compact YAML sample with comments)
  - Example unified rule schema showing combined structure
  - Field annotations (required/optional) and purposes
  - Schema changes summary (additions, enhancements, backward compatibility)
  - Comments explaining AppCAT enhancements vs Konveyor core fields

- **AppCAT Enhancements to Rulesets** (detailed explanation of AppCAT improvements)
  - Enhanced regex patterns and condition logic
  - Dependency detection integration
  - Complex multi-condition logic examples
  - Build tool integration improvements
  - Why these enhancements matter for Azure migration

- **Analysis Details** (detailed breakdown of findings)
  - **Change type breakdown**: Analysis of what types of changes make up the diverged rules
    - Field value changes only (existing fields with different values): X rules (Y%) — see field value changes table for field names and rule details
    - Condition logic changes (when field structure differs): X rules (Y%) — see change type table for rule details
    - Mixed changes (multiple types above): X rules (Y%) — see change type table for rule details
    - Field additions only (new YAML fields added): X rules (Y%) — see change type table for rule details
    - Structural changes (other complex differences): X rules (Y%) — see change type table for rule details
    - Field removals (fields removed from downstream): X rules (Y%) — see change type table for rule details
    - Description changes only (only description field differs): X rules (Y%) — see change type table for rule details
    - Regex/pattern changes (when/pattern fields differ): X rules (Y%) — see change type table for rule details
  - **Category breakdown**: Analysis by rule category showing where biggest differences are
    - Azure rules: X modified, Y unique upstream, Z unique downstream
      - Change types within Azure rules: description (X), metadata (Y), source/target (Z), regex (W)
    - Java framework rules: X modified, Y unique upstream, Z unique downstream
      - Change types within Java framework rules: description (X), metadata (Y), source/target (Z), regex (W)
    - Cloud readiness rules: X modified, Y unique upstream, Z unique downstream
      - Change types within Cloud readiness rules: description (X), metadata (Y), source/target (Z), regex (W)
    - Technology usage rules: X modified, Y unique upstream, Z unique downstream
      - Change types within Technology usage rules: description (X), metadata (Y), source/target (Z), regex (W)
    - Discovery rules: X modified, Y unique upstream, Z unique downstream
      - Change types within Discovery rules: description (X), metadata (Y), source/target (Z), regex (W)
    - Other/Uncategorized: X modified, Y unique upstream, Z unique downstream
      - Change types within Other/Uncategorized: description (X), metadata (Y), source/target (Z), regex (W)
  - **Recommended Reconciliation Plan** (actionable steps with supporting data)
    - Priority assessment based on divergence metrics
    - Phase 1: Rule synchronization and schema harmonization
    - Phase 2: Bidirectional sync workflow
    - Success metrics: "Establish an agreed upon sync cadence or new process to prevent future diversion" and "Zero breaking changes to existing Konveyor and AppCAT functionality"
  - **Data Sources**: Links to CSV files and detailed analysis
    - Per-rule analysis: identical, modified, unique upstream, unique downstream
    - Per-file analysis: file-level differences
    - Branch analysis: branches ahead of main

- **Branch Analysis** (summary of development activity)
  - Upstream branches ahead of main: count and types
  - Downstream branches ahead of main: count and types
  - Most significant branches and their purposes
  - Release branches, feature branches, dependabot branches

- **Detailed Appendix** (most specific - rule-by-rule details)
  - **Change Type Tables**: Detailed breakdown of rules by change type with field names and summaries
    - Field value changes table with field type descriptions (message, pattern, description, when, tag, effort, etc.)
    - Links to change type CSV files for each category
  - **Rule Differences by File**: Simplified table of contents linking only to files (with rule counts)
  - Changed fields show location (upstream/downstream) and one-line English summary
  - VS Code compatible markdown links

Failure behavior
- Steps continue on error; errors are logged. The orchestrator returns non-zero exit only on fatal misconfiguration (e.g., missing repos).

Agent tasks
1) Ensure `tools/`, `reports/`, `.gitignore`, and `tools/requirements.txt` exist. Create `tools/orchestrate.py` per spec.
2) **Install dependencies**: Ensure DeepDiff and PyYAML are installed. The orchestrator should detect missing dependencies and provide clear error messages.
3) Run orchestrator `--run` to produce CSVs and update `README.md`.
4) If any step must be re-run, use `--force-step` or edit/delete `tools/state.json`, or run `--reset`.
5) Keep `README.md` compact; reference CSVs for full details.
6) **Verify enriched analysis**: Ensure field names are extracted and change type CSV files are generated.

Lessons learned from first run
- YAML analysis works well with PyYAML + deepdiff for semantic comparison
- Most divergences are additive (downstream adds `message` fields, enhanced regex patterns)
- Git branch scan needs to work with local repos only (no remotes required)
- Large result sets (2K+ modified rules) are manageable with CSV outputs
- Idempotent state tracking prevents unnecessary re-runs

Lessons learned from enriched analysis
- DeepDiff integration requires proper error handling for complex YAML structures (SetOrdered objects)
- SetOrdered objects from DeepDiff.to_dict() require special handling - check for .keys() method before calling it
- Error handling improvements reduced analysis errors from 6.6% to 0.0% of modified rules
- Field name extraction from DeepDiff results provides valuable insights for reconciliation
- Change type breakdown with field names helps prioritize reconciliation efforts
- Dependency detection and installation must be robust (DeepDiff was missing initially)
- Change type CSV tables enable detailed analysis of specific rule modifications
- Field value changes (68.3% of modified rules) are the primary divergence type

Fact-checking validation
- Created independent analysis script (`/tmp/fact-check-numbers/independent_analysis.py`) using different methodology
- Independent analysis confirmed core metrics: total rules (2,941), unique upstream (137), unique downstream (241), branches ahead (11/42)
- Key discrepancy: Modified rules (1,766 independent vs 2,140 orchestrator) due to different comparison methods
- Root cause: Orchestrator uses DeepDiff for sophisticated analysis vs simple equality checks in independent analysis
- Investigation revealed AppCAT has significantly enhanced rules with metadata, descriptions, and logic that simple comparison missed
- Conclusion: Orchestrator results are more accurate and comprehensive for reconciliation planning
- Validation confirms the reconciliation analysis is reliable and ready for implementation

Inputs
- Upstream root: `rulesets/`
- Downstream root: `appcat-konveyor-rulesets/`
- Target branch for analysis: `main`

Outputs
- CSV files in `reports/`
- Updated `README.md` with compact summaries
- Logs in `tools/logs/run.log`
- State in `tools/state.json`

Runner script
- Use `tools/run.sh` for a cross-platform entrypoint:
  - Automatically clones required repositories if they don't exist locally:
    - Upstream: `https://github.com/konveyor/rulesets.git` → `rulesets/`
    - Downstream: `https://github.com/Azure/appcat-konveyor-rulesets.git` → `appcat-konveyor-rulesets/`
  - Tries `pyenv-virtualenv` first, then `.venv`, else global Python
  - `tools/run.sh --reset` to wipe state/logs/reports and regenerate
  - `tools/run.sh --force-step per_rule_diff` to rerun a specific step

Non-goals
- Do not modify upstream/downstream repos contents.
- No PR mechanics; only list branches ahead of main.

Bidirectional sync proposal (detailed in README)
- Recommend superset schema adoption; provide mapping guidance if needed. Establish periodic pulls from upstream and pushes from downstream with ruleID-centric merges.
- Propose workflow: upstream → downstream (pull new rules), downstream → upstream (push enhanced rules with AppCAT metadata)
- Schema strategy: unified superset with optional fields for AppCAT extensions (domain, category, message)
- Conflict resolution: prefer downstream enhancements for existing rules, merge new rules from upstream

Complete implementation requirements
- `tools/orchestrate.py`: Main orchestrator with all analysis steps and error handling
- `tools/run.sh`: Cross-platform runner script with virtual environment management and automatic repository cloning
- `tools/requirements.txt`: Python dependencies (PyYAML, deepdiff)
- `tools/state.json`: Idempotency state tracking
- `tools/logs/run.log`: Unified error logging
- `tools/.cache/`: Cached analysis results and normalized YAML
- `reports/`: CSV output files for all analysis results
- `reports/change_type_*.csv`: Individual change type breakdown files
- `reports/branches_*.csv`: Git branch analysis results
- `.gitignore`: Exclude logs, cache, and state files
- `README.md`: Generated analysis report with reconciliation plan
- `DESIGN.md`: Complete technical documentation with processing flow diagrams

Key implementation details
- Handle YAML files that are actually directories (filter with is_file())
- Implement robust DeepDiff error handling for SetOrdered objects
- Generate comprehensive change type analysis with field names
- Create detailed CSV reports for each change type category
- Implement branch scanning with remote branch support
- Generate human-readable change summaries for modified rules
- Support both per-rule semantic and per-file syntactic comparison
- Cache normalized YAML content for performance
- Provide reset functionality to clear all generated content

Implement the files and scripts, then run the orchestrator.


