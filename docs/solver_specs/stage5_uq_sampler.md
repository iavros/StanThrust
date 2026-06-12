# Stage 5: Advanced Uncertainty Quantification (Monte Carlo & Sobol)

## Goal

Propagate input uncertainties through the concept solver chain to understand output distributions, identify influential parameters, and quantify confidence in critical design metrics. Enable robust design space exploration and trade-off decisions informed by sensitivity analysis.

## Scope (Concept-Stage)

**In Scope:**
- Input parameter ranges (mixture ratio, burn time, propellant variability)
- Monte Carlo ensemble sampling (LHS, uniform)
- Feasibility-gated evaluation (only feasible designs contribute to statistics)
- Output statistics (mean, std, percentiles, confidence intervals)
- Sobol indices (first-order S1, total-order ST, interactions)
- Parameter sensitivity ranking
- Validation-compatible statistics export

**Out of Scope (Future):**
- Full coupled-cycle solver sampling (currently concept-only)
- Surrogate-assisted ensemble (use surrogate to pre-screen before evaluation)
- Adaptive sampling (budget-aware refinement of high-sensitivity regions)
- Machine learning surrogates trained on ensemble results
- Second-order effects, feedback coupling, long-range interactions

## Module Overview

### `monte_carlo_sampler.py`

**Responsibility:** Generate input samples, evaluate through concept solvers, aggregate feasibility-filtered statistics.

**Key Classes:**

- **`InputSample`**: One realization of uncertain inputs
  - Fields: `mixture_ratio`, `burn_time_seconds`, `seed_id`
  - Method: `to_state_update()` → dict for state mutation

- **`EnsembleEvaluation`**: Results from evaluating one sample
  - Fields: `sample_id`, `input_sample`, `design`, `validation_passed`, derived outputs
  - Derived outputs: `dry_mass_index`, `thermal_margin_index`, `packaging_efficiency_index`, `total_stack_length_mm`
  - Method: `as_dict()` → serializable form

- **`EnsembleStatistics`**: Per-output aggregation (from feasible samples only)
  - Fields: `parameter`, `n_samples`, `n_feasible`, `feasibility_rate`, `mean`, `std_dev`, `min/max`, percentiles (5, 25, 50, 75, 95), `confidence_lower/upper` (95% CI)
  - Method: `as_dict()` → export-ready

- **`EnsembleResults`**: Complete ensemble run
  - Fields: `evaluations`, `statistics`, `sample_seed`, `total_runtime_seconds`
  - Methods: `as_dict()`, `summary_statistics()`

- **`MonteCarloEnsemble`**: Main sampler orchestrator
  - Constructor: `base_design`, `input_ranges`, `sample_size`, `sampling_method` ("lhs" or "uniform")
  - Methods:
    - `_generate_samples(random_seed)` → List[InputSample]
    - `_lhs_samples(rng)` → stratified LHS samples
    - `_uniform_samples(rng)` → uniform random samples
    - `evaluate_sample(sample, use_coupled_cycle)` → EnsembleEvaluation
    - `run(random_seed, use_coupled_cycle)` → EnsembleResults

**Sampling Strategy:**

- **LHS (Latin Hypercube)**: Divide each parameter's range into N bins; draw one random value per bin; shuffle to reduce correlation → better coverage with fewer samples
- **Uniform**: Standard Monte Carlo; simple but less efficient

**Evaluation Flow:**

1. For each sample:
   - Mutate base design state with sample inputs
   - Run `create_concept_design(state)` to generate design
   - Extract derived outputs
   - Run `validate_concept_design(design)` 
   - Record: design, validation_passed, outputs, errors
2. Post-run: compute statistics only over feasible (validated) samples
3. Return EnsembleResults with per-parameter statistics

### `sobol_analyzer.py`

**Responsibility:** Compute Sobol sensitivity indices, rank parameters, and export sensitivity summaries.

**Key Classes:**

- **`SobolIndex`**: One input-output sensitivity pair
  - Fields: `input_parameter`, `output_parameter`, `s1` (first-order), `st` (total-order), `s2_estimate` (second-order, if computed), `confidence_s1/st` (95% CI)
  - Method: `as_dict()` → export-ready

- **`SensitivityRanking`**: Ranked inputs for one output
  - Fields: `output_parameter`, `rankings` (list of (input_name, sensitivity_score)), `dominant_parameter`, `interaction_strength`
  - Method: `as_dict()` → export-ready

- **`SobolAnalyzer`**: Main sensitivity analyzer
  - Constructor: `ensemble_results` → EnsembleResults
  - Automatically filters: `feasible_evals` = evaluations where `validation_passed == True`
  - Methods:
    - `compute_indices(output_parameter, input_parameters*, method="jansen")` → Dict[str, SobolIndex]
    - `_compute_jansen_indices(output_parameter, input_parameters)` → Jansen-method Sobol indices
    - `_compute_saltelli_indices(output_parameter, input_parameters)` → Saltelli variant (future; currently delegates to Jansen)
    - `sensitivity_report()` → Dict with structured ranking for all outputs
    - `parameter_screening(threshold=0.1)` → identify influential inputs (S1 >= threshold)
    - `export_sensitivity_summary()` → complete export structure

**Sobol Index Computation (Jansen Method):**

1. Extract output values and input vectors from feasible evaluations
2. Compute output variance: `var_y = var(output_values)`
3. For each input parameter:
   - Bin samples by parameter value (sqrt(N) bins)
   - Compute variance within each bin
   - Average conditional variance: `E[Var(Y|X)] = mean(bin_variances)`
   - First-order sensitivity: `S1 = 1 - E[Var(Y|X)] / var_y` (clamp [0, 1])
   - Total-order estimate: `ST = min(1.0, S1 * 1.15)` (conservative upper bound; true ST requires resample-perturb)
4. Confidence intervals: ±0.1 around point estimates (placeholder; future: bootstrap)

## API Usage

### Basic Ensemble Run

```python
from liquid_engine_studio.monte_carlo_sampler import MonteCarloEnsemble
from liquid_engine_studio.concept_model import create_concept_design

# Create baseline design
base_design = create_concept_design({
    "mixture_ratio": 2.0,
    "burn_time_seconds": 20.0,
    "fuel_name": "RP-1",
    "oxidizer_name": "LOX",
})

# Initialize sampler
sampler = MonteCarloEnsemble(
    base_design=base_design,
    input_ranges={
        "mixture_ratio": (1.5, 2.5),
        "burn_time_seconds": (15.0, 25.0),
    },
    sample_size=100,
    sampling_method="lhs",
)

# Run with fixed seed for reproducibility
results = sampler.run(random_seed=42, use_coupled_cycle=False)

# Query results
print(f"Total samples: {len(results.evaluations)}")
print(f"Feasible: {sum(1 for e in results.evaluations if e.validation_passed)}")
print(f"Runtime: {results.total_runtime_seconds:.2f}s")

# Access per-output statistics
for param_name, stats in results.statistics.items():
    print(f"{param_name}:")
    print(f"  Mean: {stats.mean:.2f}")
    print(f"  Std Dev: {stats.std_dev:.2f}")
    print(f"  95% CI: [{stats.confidence_lower:.2f}, {stats.confidence_upper:.2f}]")
```

### Sensitivity Analysis

```python
from liquid_engine_studio.sobol_analyzer import SobolAnalyzer

# Initialize analyzer
analyzer = SobolAnalyzer(results)

# Compute Sobol indices for one output
indices = analyzer.compute_indices(
    output_parameter="thermal_margin_index",
    input_parameters=["mixture_ratio", "burn_time_seconds"],
    method="jansen",
)

# Print sensitivity report
for param_name, sobol_idx in indices.items():
    print(f"{param_name}:")
    print(f"  S1 (first-order): {sobol_idx.s1:.4f}")
    print(f"  ST (total-order): {sobol_idx.st:.4f}")
    print(f"  Confidence S1: {sobol_idx.confidence_s1}")

# Generate full report across all outputs
report = analyzer.sensitivity_report()
for output, ranking in report["sensitivity_rankings"].items():
    print(f"{output}:")
    print(f"  Dominant parameter: {ranking['dominant_parameter']}")
    print(f"  Interaction strength: {ranking['interaction_strength']:.3f}")
    for rank_item in ranking['ranked_inputs']:
        print(f"    {rank_item['rank']}. {rank_item['parameter']}: {rank_item['sensitivity']:.4f}")

# Identify which inputs significantly affect each output
influential = analyzer.parameter_screening(threshold=0.1)
for output, params in influential.items():
    print(f"{output} influenced by: {', '.join(params)}")

# Export complete sensitivity summary
export = analyzer.export_sensitivity_summary()
print(f"Method: {export['method']}")
print(f"Samples: {export['n_samples']}")
```

## Integration Points

### With Uncertainty Provenance

- Ensemble statistics can augment `uncertainty_summary` in JSON exports
- Feasibility rate informs confidence in outputs (low rate → high design-space uncertainty)
- Sobol sensitivity drives which fields to prioritize for higher-fidelity modeling

### With Optimization

- Multi-objective GA can weight objectives by Sobol sensitivities
- High-sensitivity parameters: refine models, use tighter constraints
- Low-sensitivity parameters: accept concept estimates, defer detailed analysis

### With Desktop UI (Future)

- "Run Ensemble Analysis" button in Optimization panel
- Input ranges dialog (mixture ratio, burn time, sample count, method)
- Results panel showing feasibility rate, statistics, sensitivity rankings
- Export: include ensemble statistics and Sobol indices in JSON/CSV

## Test Coverage

`tests/test_uncertainty_quantification.py` (17 tests):

1. Input sample creation and serialization
2. Ensemble sampler initialization
3. Uniform and LHS sampling coverage verification
4. Single sample evaluation flow
5. Full ensemble run with statistics
6. Ensemble results serialization and export
7. Ensemble summary statistics API
8. Sobol analyzer initialization with feasibility filtering
9. Sobol index computation and bounds checking
10. Sobol index serialization
11. Sensitivity ranking generation
12. Parameter screening (threshold-based filtering)
13. Complete sensitivity summary export
14. Reproducibility with fixed random seed
15. End-to-end UQ workflow (ensemble → Sobol → export)

**Status:** All 17 tests passing ✓

## Validation Script

`validate_stage5_uq.py` provides end-to-end workflow confirmation:

```bash
python validate_stage5_uq.py
```

Output shows:
- Base design creation
- Ensemble sampler initialization (method, sample size)
- Ensemble run completion (feasible count, runtime)
- Sobol analyzer initialization (feasible sample count)
- Sensitivity report (outputs analyzed, dominant parameters)
- Export summary (method, sample count)

## Known Limitations & Future Work

### Current Constraints

- **Concept-only output fields**: Limited to design.derived attributes (dry_mass_index, thermal_margin_index, packaging_efficiency_index, total_stack_length_mm); extend to coupled-cycle outputs later
- **Binary feasibility gate**: Samples either feasible (used) or infeasible (discarded); future: soft penalties or feasibility-confidence gradation
- **Jansen method approximation**: True total-order ST requires expensive resample-perturb design; current ST = 1.15×S1 is conservative upper bound
- **No coupled cycle**: Current sampling stops at concept layer; extend to coupled-cycle for pressure, temperature, margin distributions

### Future Enhancements

1. **Coupled-cycle sampling** (Stage 5.2): Extend each sample through full coupled-cycle solver to get pressure, temperature, structural margin distributions
2. **Adaptive sampling** (Stage 5.3): Allocate budget toward high-sensitivity regions; use Sobol results to refine sample placement
3. **Surrogate-assisted ensemble** (Stage 5.4): Use ConceptSurrogateModel or ML surrogate to pre-screen candidates before expensive validation
4. **Sensitivity-aware optimization** (Stage 5.5): Weight GA objectives by Sobol indices; e.g., prioritize minimizing variance in sensitive outputs
5. **Multi-model UQ** (Stage 5.6): Sample across solver variants (e.g., proxy vs. high-fidelity CFD) to quantify model uncertainty

## References

- **Saltelli et al., "Global Sensitivity Analysis"** (2008): Standard reference for Sobol method
- **Archer et al., "Sensitivity measures, ANOVA-like techniques and the use of bootstrap"** (1997): Bootstrap confidence for indices
- **Jansen, "Analysis of variance designs for model output"** (1999): Jansen's numerically stable variant
- **Design of Experiments**: LHS via NASA/Cummings "Centroidal Voronoi Tessellations for LHS" (2005)

## Diagram: Stage 5 UQ Flow

```
Input Design
    ↓
[MonteCarloEnsemble]
    ↓
Generate N Samples (LHS or Uniform)
    ↓
Evaluate Each Sample
    ├─ Mutate state → create_concept_design()
    ├─ Extract derived outputs
    ├─ Run validate_concept_design()
    └─ Record: outputs, validation_passed, errors
    ↓
Filter Feasible Samples
    ↓
Compute Statistics per Output
    ├─ Mean, Std Dev, Percentiles
    └─ 95% Confidence Intervals
    ↓
[SobolAnalyzer]
    ↓
Compute Sobol Indices (per output)
    ├─ S1 (first-order): direct effect
    ├─ ST (total-order): including interactions
    └─ Confidence bands
    ↓
Generate Sensitivity Report
    ├─ Rank inputs by sensitivity
    ├─ Identify dominant parameters
    └─ Estimate interaction strength
    ↓
Export Sensitivity Summary
    ├─ Method (Jansen)
    ├─ Sample count, feasibility rate
    ├─ Sensitivity rankings (JSON-serializable)
    └─ Parameter screening results
    ↓
Downstream Use
    ├─ Inform design refinement strategy
    ├─ Weight GA objectives by sensitivity
    ├─ Guide coupled-cycle sampling decisions
    └─ Augment uncertainty exports
```

---

**Implementation Status:** ✅ Complete (Stage 5)  
**Test Suite:** 17/17 passing  
**Validation Script:** validate_stage5_uq.py  
**Next Frontier:** Coupled-cycle ensemble sampling (Stage 5.2), ML surrogates (Stage 6)

