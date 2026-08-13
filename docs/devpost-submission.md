# Devpost submission — draft

In English, for the same reason `README.md` is: that's the language of the
judging and of the Arm community. Switch it if the form asks otherwise.

**Every `⟨…⟩` below is a placeholder that must be replaced with a number that
came out of a real run on real Arm silicon.** Do not submit with any of them
still in place — grep for `⟨` before pasting. If Phase A produces a smaller
speedup than hoped, put the real one in; the honest framing is already written
into the text below and it still reads as a strong submission.

---

## 1. Project summary — what it is, and why it should win

Arm shipped **Performix** in April 2026 and asked the community to build new
recipes for it. `apxtune` is that contribution, plus the agent that drives it.

Arm silicon ships capabilities most inference stacks leave on the floor. A
Neoverse V2 has `i8mm` (SMMLA) and `bf16`, but whether your GEMM actually
*reaches* those instructions depends on a chain of decisions — build flags,
quantization format, thread count, allocator, core affinity — that interact
with each other and change with every core generation. The usual answer is a
blog post with a table of magic flags, stale the moment new silicon ships and
never right for your model size anyway.

`apxtune` replaces that table with a procedure. It profiles an inference
workload on the machine in front of it, searches the configuration space for
the best setup *for that specific silicon*, keeps a change only if it survives
a statistical test, and then proves **where each percent came from** using
hardware counters.

Three things make it worth a prize rather than a star:

1. **Arm built the instrument; this is the agent that plays it.** The
   submission includes `recipes/llm-inference.recipe.json`, a custom Performix
   recipe that splits prefill (compute-bound GEMM) from decode (memory-bound
   GEMV). Generic recipes average the two phases and hide whichever one is
   actually the bottleneck. It is donable to the community as-is.
2. **It refuses to lie.** Every number is a median over N runs with a bootstrap
   95% CI. Every accepted change cleared three gates: direction, minimum effect
   size, Mann-Whitney at α=0.05. Rejected changes are printed too — a tuner
   that only publishes its wins has no denominator. And the headline speedup is
   not a subtraction of two measurements taken hours apart: when the search
   ends, both configurations are measured again from scratch, **interleaved**
   (A,B,B,A,A,B…), so environmental drift lands on both branches instead of
   being credited as improvement. That single pre-declared comparison is what
   gets published; the per-step waterfall is labelled as indicative.
3. **The output outlives the run.** A portable profile keyed on the int8 GEMM
   path, a runnable script, a self-contained HTML report, and a GitHub Action
   that blocks perf regressions on free Arm runners.

## 2. Functionality / result — what it does, what you walk away with

One command:

```bash
apxtune tune workloads/llama-bench.toml --out results/
```

On ⟨instance type, e.g. AWS c8g.4xlarge, Neoverse-V2, 16 vCPU⟩ running
llama.cpp with ⟨model, e.g. Llama-3.2-3B-Instruct⟩, this took decode throughput
from **⟨baseline⟩ to ⟨tuned⟩ tok/s — ⟨N.NN⟩×**, validated head-to-head with
⟨k⟩ interleaved pairs at p=⟨p⟩. Same model, same weights, no code changes.

Where it came from:

| change | from | to | effect | p |
|---|---|---|---:|---:|
| ⟨build⟩ | ⟨no-kleidiai⟩ | ⟨KleidiAI⟩ | ⟨+xx.x%⟩ | ⟨0.000x⟩ |
| ⟨model⟩ | ⟨Q4_K_M⟩ | ⟨Q4_0⟩ | ⟨+xx.x%⟩ | ⟨0.000x⟩ |
| ⟨threads⟩ | ⟨4⟩ | ⟨16⟩ | ⟨+x.x%⟩ | ⟨0.00xx⟩ |

⟨If Performix was usable, keep this paragraph and fill it in; if the PMU was
virtualized away, delete it and say so plainly instead — the tool reports the
absence rather than hiding it.⟩ The Performix capture shows the share of int8
matmul instructions (SMMLA/i8mm) going from ⟨x.x%⟩ to ⟨yy.y%⟩. That is
hardware-level evidence the speedup came from using Arm's ISA, not from a
generic change that would have helped on x86 too.

Deliverables, all in the repo:

- `profiles/⟨key⟩.json` — the winning config plus per-step attribution and the
  provenance of the number (`validation.method`)
- `profiles/⟨key⟩.html` — self-contained report, no CDN, opens over `scp`
- `recipes/llm-inference.recipe.json` — the custom Performix recipe
- `.github/workflows/arm-perf-guard.yml` — CI that fails a PR on regression,
  on GitHub's free `ubuntu-24.04-arm` runners

## 3. Setup instructions

Reproducible end to end on any Arm64 box. Python 3.11+, zero runtime
dependencies — it installs on a fresh instance with no network.

```bash
# 1. Get the tool
git clone ⟨REPO_URL⟩ && cd apxtune
pip install -e .

# 2. Check the machine can measure honestly
#    (PMU access, frequency governor, Performix presence, core count)
apxtune doctor

# 3. Verify the whole pipeline in ~2 minutes, no downloads
apxtune tune workloads/demo-synthetic.toml --out results/

# 4. The real thing
bash scripts/setup_arm.sh                       # deps, governor, PMU access
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
# put Q4_0 and Q4_K_M GGUFs of your model in ~/llama.cpp/models/
apxtune tune workloads/llama-bench.toml --dry-run    # sanity-check commands
apxtune tune workloads/llama-bench.toml --out results/
open results/*.html

# 5. Apply the result anywhere with the same core
apxtune match --registry profiles/
apxtune apply --profile profiles/⟨key⟩.json
```

Budget ⟨1–2⟩ h for step 4 depending on grid and model size. Tests:
`python -m pytest -q` (33 tests, including a measured false-positive rate for
the Mann-Whitney implementation over 2000 trials).

Hardware used for the numbers above: ⟨instance type, vCPU count, kernel,
llama.cpp commit⟩.

---

## Pre-submission checklist

- [ ] No `⟨` left anywhere in the text pasted into Devpost
- [ ] Repo is **public** on GitHub
- [ ] Apache-2.0 visible in the repo's **About** panel (not just `LICENSE`)
- [ ] Repo contains all source, assets and instructions — nothing external
- [ ] The three sections above filled into the Devpost form
- [ ] Video under 3 min, public on YouTube/Vimeo, showing the tool running on
      the real device: `apxtune doctor` → `apxtune tune` → the HTML waterfall →
      the Performix instruction mix if available. No copyrighted music, no
      third-party marks.
- [ ] `profiles/⟨key⟩.json` has `validation.method = "interleaved_ab"` and
      `validation.significant = true`
- [ ] `python -m pytest -q` green on the submission commit
