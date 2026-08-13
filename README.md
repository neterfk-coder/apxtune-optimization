# apxtune

**Your Arm box, tuned in minutes. Same model, same weights, measurably faster — with hardware counters as the proof.**

`apxtune` profiles an inference workload on Arm, searches the configuration
space for the best setup *for that specific silicon*, and only keeps a change
if it survives a statistical test. It emits a reusable profile, a runnable
script, and a report that shows **where each percent came from**.

```
apxtune tune workloads/llama-bench.toml

▸ baseline  build=no-kleidiai model=Q4_K_M threads=4 …
  decode_tps = 8.41 [8.28, 8.55] n=7 tok/s

  ✓ build:   no-kleidiai → KleidiAI      +23.8%  (p=0.0006)
  ✓ model:   Q4_K_M → Q4_0               +41.2%  (p=0.0002)
  ✓ threads: 4 → 16                       +9.6%  (p=0.0031)
  ✗ ubatch:  discarded — +1.4% below the 2.0% threshold
  ✗ omp_bind: discarded — +3.1% but p=0.412 (noise)

▸ final validation  baseline vs best, 7 interleaved pairs
  8.41 → 15.02 tok/s   1.79×  (p=0.0002)
```

*(Shape of real output. The numbers above are illustrative — every number
this tool prints comes from a measurement on your machine, and so must yours.
See [Reproducing](#reproducing).)*

---

## Why this exists

Arm silicon ships capabilities that most inference stacks leave on the floor.
A Neoverse V2 has `i8mm` (SMMLA) and `bf16`; whether your GEMM actually
*reaches* those instructions depends on a chain of decisions — build flags,
quantization format, thread count, allocator, core affinity — that interact
with each other and change with every core generation.

The usual response is a blog post with a table of magic flags. That table is
stale the moment a new core ships, and it was never right for your model size
anyway.

`apxtune` replaces the table with a procedure.

**And it refuses to lie to you.** Most benchmark tooling reports one run.
Every number here is a median over N runs with a bootstrap 95% CI, and every
accepted change had to clear three gates: right direction, minimum effect
size, and a Mann-Whitney test at α=0.05. The rejected changes are printed too
— a tuner that only publishes its wins has no denominator.

And the headline number is not a subtraction of old measurements. A tuning run
can take hours; a baseline measured at the start and an optimum measured at the
end are separated by however much thermal drift, frequency scaling and noisy
neighbours happened in between, and none of that is the tuner's doing. So when
the search finishes, `apxtune` measures both configurations again from scratch,
**interleaved** — A,B,B,A,A,B… — and *that* single, pre-declared comparison is
what gets published.

---

## What you get

| Artifact | What it's for |
|---|---|
| `results/<key>.json` | Portable profile: the winning config + per-step attribution |
| `results/<key>.html` | Self-contained report — no CDN, opens over `scp` |
| `results/<key>.sh` | The optimized command, ready to run |
| `recipes/llm-inference.recipe.json` | Custom **Arm Performix** recipe for LLM inference |
| `.github/workflows/arm-perf-guard.yml` | CI that blocks perf regressions, on free Arm runners |
| `profiles/` | Community registry — start from someone else's optimum |

---

## Install

Python 3.11+. **Zero runtime dependencies** — it installs on a fresh instance
with no network.

```bash
git clone https://github.com/YOUR_USER/apxtune && cd apxtune
pip install -e .
apxtune doctor
```

`doctor` tells you whether this machine can measure honestly: PMU access,
frequency governor, Performix presence, core count.

---

## Quickstart

### 1. Verify the whole pipeline in ~2 minutes, with no downloads

```bash
apxtune tune workloads/demo-synthetic.toml --out results/
```

A self-contained synthetic workload with real, non-trivial optima. It exists
so you can confirm search, statistics, and reporting all work before spending
an hour on a 2 GB model.

### 2. Tune llama.cpp

```bash
bash scripts/setup_arm.sh                      # deps, governor, PMU access
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
# put Q4_0 and Q4_K_M GGUFs in ~/llama.cpp/models/
apxtune tune workloads/llama-bench.toml --out results/
open results/*.html
```

### 3. Apply the result anywhere

```bash
apxtune match --registry profiles/    # is there a profile for this core?
apxtune apply --profile results/Neoverse-V2.64c.i8mm+bf16.json
```

---

## How it works

**Coordinate descent in two phases**, not a grid sweep and not Bayesian
optimization. A real inference grid is 100–5000 points; the axes are nearly
separable; and — most importantly — coordinate descent produces a *readable
attribution*: how much each decision contributed on its own.

```
explore     3 runs per value, sweeps one axis, ranks candidates
confirm     full N runs, head-to-head against the incumbent
gate        direction ✓  effect ≥ 2% ✓  p < 0.05 ✓   → accept, else revert
validate    once, at the end: baseline vs best, interleaved → the published number
```

Axes are ordered by expected impact: `prelude` (rebuild) → `arg` → `env`.
Rebuilding with KleidiAI or switching quantization moves the optimum of
everything downstream, so tuning `--ubatch-size` first would throw the work
away.

### Adding a workload takes no Python

A workload is a TOML file: a command template, regexes to pull metrics out of
its stdout, and the axes to move. `apxtune` knows nothing about llama.cpp.

```toml
[[axis]]
name = "model"
kind = "arg"
requires = ["i8mm"]        # axis is pinned on cores without the feature
note = "Q4_0 enables repacking to SMMLA kernels"
values = ["models/m-Q4_K_M.gguf", "models/m-Q4_0.gguf"]
```

`requires` is what makes profiles portable: an axis that needs `i8mm` collapses
to its safe value on a core that doesn't have it, instead of failing.

Tokens like `$core_sweep`, `$big_cluster` and `$physical_cores` resolve against
the detected topology, so one TOML works on a 64-core Graviton4 and a
Raspberry Pi 5.

---

## Arm-specific, not generic

`apxtune detect` reads `MIDR_EL1` per CPU and the `Features` line to work out
what the silicon can actually do:

```
core            Neoverse-V2
cores           64 physical / 64 logical / 1 NUMA node
int8 GEMM path  NEON i8mm (SMMLA)
features        asimd asimddp i8mm bf16 sve sve2 svei8mm svebf16 atomics
```

The **int8 GEMM path** is the single most predictive fact about what the
optimum will look like, so it's what the profile registry matches on — more
useful than the core name, because two different cores with the same GEMM
path want the same configuration.

On heterogeneous SoCs (big.LITTLE), clusters are detected separately and
`$big_cluster` gives you a `taskset` mask for the performance cores. Mixing
clusters is one of the most common silent throughput killers on mobile Arm.

---

## Arm Performix integration

[Arm Performix](https://developer.arm.com/servers-and-cloud-computing/arm-performix)
is Arm's performance analysis toolkit for Neoverse. `apxtune` uses it to
capture *why* a change worked, not just that it did — it records the
`instruction-mix` and `microarch` recipes before and after tuning and puts the
delta in the report.

The interesting line in that report:

> The share of int8 matmul instructions (SMMLA/i8mm) went from **0.2%** to
> **34.7%**.

That is hardware-level evidence that the speedup came from using Arm's ISA —
not from a generic change that would have helped on x86 too. It's the
difference between "it got faster" and "it got faster *because of this*".

`recipes/llm-inference.recipe.json` is a **custom Performix recipe** that
splits prefill (compute-bound GEMM) from decode (memory-bound GEMV). Generic
recipes average the two phases and hide whichever one is actually your
bottleneck. It's a draft against Performix 2026.1 — validate with
`apx recipe validate` and adjust event names to your core.

**Performix is optional.** No `apx`, or a VM with the PMU virtualized away?
`apxtune` still tunes and still reports; the report just says the
microarchitecture evidence is missing instead of quietly omitting it.

---

## CI: stop perf regressions at the PR

`.github/workflows/arm-perf-guard.yml` runs on `ubuntu-24.04-arm` — GitHub's
native Arm runners, free on public repos, so this needs no infrastructure of
your own. It tunes on every PR, writes the attribution table to the job
summary, uploads the profile, and fails the build via `--fail-under` if
throughput drops below your floor.

```yaml
- run: apxtune tune "$WORKLOAD" --fail-under 1.0 --min-effect 3.0
```

---

## Reproducing

Every claim this repo makes is reproducible in one command on a machine you
control. There are no benchmark numbers checked into this README, on purpose:
performance claims that can't be re-run on the reader's hardware aren't worth
printing.

```bash
apxtune doctor                                        # is this box measurable?
apxtune tune workloads/demo-synthetic.toml --out r/   # ~2 min, no downloads
python -m pytest -q                                   # 33 tests
```

The test suite checks the statistics themselves, not just the plumbing: the
false-positive rate of the Mann-Whitney implementation is measured over 2000
trials and asserted to sit near the nominal 5%, and the final validation is
tested against the case it exists for — a search that reports 2.0× where the
interleaved measurement finds 1.2×.

---

## Limitations, stated plainly

- **Configuration space, not code generation.** `apxtune` finds the best way
  to run the software you have. It does not write kernels.
- **Noise dominates on shared instances.** If baseline RSD is above 5%, the
  report says so and the profile shouldn't be published. Burst-credit VMs and
  noisy neighbours will waste your time.
- **Coordinate descent can settle in a local optimum** when axes interact
  strongly. Ordering preludes first mitigates the worst case; `--passes 3`
  helps; it is not a global search.
- **The per-step attribution is indicative, not certified.** A run performs
  roughly a dozen comparisons at α=0.05, with no multiplicity correction, so
  expect the occasional lucky accept in the waterfall. The final interleaved
  validation is one pre-declared comparison and carries no such caveat — which
  is exactly why the headline number comes from there and not from summing the
  waterfall.
- **The Performix recipe is a draft** against the 2026.1 schema. Validate it.
- **The synthetic workload is a harness test, not a benchmark.** It proves
  the tool works. It says nothing about your model.

---

## Layout

```
src/apxtune/
  isa.py         MIDR/feature/topology detection
  stats.py       bootstrap CI, Mann-Whitney, the three acceptance gates
  space.py       TOML workload + configuration space
  bench.py       warmup, N runs, metric extraction
  search.py      two-phase coordinate descent, attribution
  performix.py   apx wrapper, PMU availability, instruction mix, top-down
  profiles.py    portable profile registry
  report.py      self-contained HTML report
workloads/       llama-bench.toml, demo-synthetic.toml
recipes/         custom Performix recipe for LLM inference
profiles/        community registry
```

---

## License

Apache-2.0. See [LICENSE](LICENSE).

Contributions — especially profiles from silicon not covered yet — are
welcome: [CONTRIBUTING.md](CONTRIBUTING.md).
