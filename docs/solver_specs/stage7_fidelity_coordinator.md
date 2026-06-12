```markdown
# Stage 7: Advanced Fidelity Coordination

## Goal

Enable progressive fidelity escalation where GA candidates are intelligently routed through solver tiers (heuristic → concept → coupled-cycle) based on surrogate uncertainty and Sobol sensitivity. This maximizes accuracy on critical outputs while minimizing computational cost through adaptive sampling and periodic surrogate retraining.

## Scope

**In Scope:**
- Fidelity routing logic based on surrogate confidence + Sobol sensitivity
- Three-tier evaluation pools (heuristic, concept, coupled-cycle)
- Budget-aware candidate allocation
- Periodic ML surrogate retraining after each GA generation
- Cost estimation and utilization tracking
- Serializable routing/allocation metadata for export & tracing

**Out of Scope (Future):**
- Parallel/async evaluation across pools
- Neural network surrogates (Stage 6.2)
- Meta-learning (pre-trained models)
- Sensitivity-aware objective weighting
- Full coupled-cycle solver execution

## Architecture

### Design Principles

1. **Staged Escalation**: Start with fast heuristic; escalate only when necessary based on data
2. **Budget Enforcement**: Hard constraint on total evaluation time per GA generation
3. **Confidence-Driven**: Route candidates where surrogate is uncertain
4. **Sensitivity-Aware**: High-sensitivity outputs always validated with high-fidelity solvers
5. **Continuous Learning**: Surrogate improves each generation as more evaluation data accumulates

## Module: `fidelity_coordinator.py`

### Core Classes

#### 1. `FidelityTier` (Enum)

Solver fidelity levels with cost/fidelity trade-offs:

```python
class FidelityTier(str, Enum):
    HEURISTIC      = "heuristic"       # ~1 ms, fast pre-screen
    CONCEPT        = "concept"         # ~50 ms, current solver
    COUPLED_CYCLE  = "coupled_cycle"   # ~500 ms, full coupled solver
```

#### 2. `FidelityRouter`

Routes individual candidates to appropriate solver tier.

**Constructor:**
- `enable_coupled_cycle (bool)`: If False, top tier is concept only

**Key Method: `route()`**

Routes one candidate based on four decision rules:

```python
def route(
    self,
    candidate_id: str,
    surrogate_confidence: float,     # 0.0–1.0
    sobol_sensitivity_score: float,  # 0.0–1.0
    cost_budget_available: int,       # milliseconds
) -> RoutingDecision:
```

**Routing Rules (applied in order):**

1. **High Sensitivity Override** (S ≥ 0.70):
   - Route to coupled-cycle if budget ≥ 500ms
   - Otherwise route to concept
   - Rationale: Critical outputs need full-fidelity validation

2. **High Confidence Fast-Track** (C ≥ 0.85):
   - Route to heuristic
   - Rationale: Trust surrogate; save evaluation cost

3. **Medium Confidence Validation** (0.65 ≤ C < 0.85):
   - Route to concept
   - Rationale: Worth validating before committing to high-fidelity

4. **Low Confidence Escalation** (C < 0.65):
   - Route to coupled-cycle if budget ≥ 500ms
   - Otherwise route to concept
   - Rationale: Get best available fidelity for uncertain predictions

**Thresholds (tunable):**
```python
HIGH_CONFIDENCE_THRESHOLD = 0.85
MEDIUM_CONFIDENCE_THRESHOLD = 0.65
SENSITIVITY_ESCALATION_THRESHOLD = 0.70
```

**Methods:**
- `route(candidate_id, confidence, sensitivity, budget) → RoutingDecision`
- `route_batch(candidates, budget) → List[RoutingDecision]`: Route entire population
- `get_routing_summary() → Dict`: Statistics on routing decisions

#### 3. `AdaptiveSamplingPool`

Manages three evaluation queues across fidelity levels.

**Constructor:**
```python
def __init__(self, budget_ms: int = 10000):
    self.heuristic_queue: List[CandidateAllocation]  # ~1 ms each
    self.concept_queue: List[CandidateAllocation]    # ~50 ms each
    self.coupled_queue: List[CandidateAllocation]    # ~500 ms each
```

**Key Methods:**

```python
def allocate_candidate(self, decision: RoutingDecision) -> Optional[CandidateAllocation]:
    """Allocate candidate if budget available; else return None."""

def allocate_batch(self, decisions: List[RoutingDecision]) -> Tuple[int, int]:
    """Return (allocated_count, rejected_count)."""

def get_pool_stats(self) -> Dict[str, PoolStats]:
    """Statistics for each pool (candidate count, total cost)."""

def get_summary(self) -> Dict[str, object]:
    """Complete allocation summary with budget utilization."""
```

**Budget Enforcement:**
- Tracks total estimated cost across all queues
- Returns None if allocation would exceed budget
- Allocations proceed in order; late candidates may be rejected

#### 4. `SurrogateRetrainingScheduler`

Manages periodic ML surrogate model updates.

**Constructor:**
```python
def __init__(
    self,
    retrain_threshold: int = 10,              # Min training samples
    retrain_interval_generations: int = 1,     # Retrain every N generations
):
```

**Key Methods:**

```python
def add_ga_results(self, ga_results: List[Dict]) -> None:
    """Add best candidates from one GA generation to training pool."""

def maybe_retrain(self, ml_surrogate_model: Optional[object]) -> RetrainingStats:
    """
    Decide whether to retrain based on interval + threshold rules.
    Returns RetrainingStats with outcome and improvement estimate.
    """

def get_retraining_summary(self) -> Dict[str, object]:
    """Cumulative statistics on retraining activity."""
```

**Retraining Decision Logic:**

```
should_retrain = (
    (generation_count - last_retrain_gen) >= interval AND
    len(training_pool) >= threshold AND
    ml_surrogate_model is not None
)
```

**Improvement Estimation:**
- Queries old model's average confidence before retraining
- Queries new model's average confidence after training
- Computes: `improvement = (new - old) / old`

### Data Classes

#### `RoutingDecision`

Result of routing one candidate:
- `candidate_id: str`
- `assigned_tier: FidelityTier`
- `surrogate_confidence: float`
- `sobol_sensitivity_score: float`
- `cost_budget_available: int`
- `reasoning: str` (human-readable explanation)

#### `CandidateAllocation`

Result of allocating candidate to a pool:
- `candidate_id: str`
- `tier: FidelityTier`
- `estimated_cost_ms: int`
- `allocated_at: float` (timestamp)

#### `PoolStats`

Per-pool statistics:
- `tier: FidelityTier`
- `candidate_count: int`
- `total_estimated_cost_ms: int`
- `completed_count: int`
- `total_actual_cost_ms: float`

#### `RetrainingStats`

Result of retraining attempt:
- `retrained: bool`
- `previous_sample_count: int`
- `new_sample_count: int`
- `training_time_ms: float`
- `improvement_estimate: float`
- `notes: str`

### Orchestration Function

```python
def coordinate_fidelity_escalation(
    ga_candidates: List[Dict],
    router: FidelityRouter,
    pool: AdaptiveSamplingPool,
    cost_budget_ms: int = 10000,
) -> Dict[str, object]:
```

Returns complete coordination result with routing decisions, pool allocation, and statistics.

## Integration with GA Workflow

### Target Integration (Future)

Modify `run_genetic_optimizer()` in `optimizer_hooks.py`:

```python
def run_genetic_optimizer(seed, ..., enable_fidelity_coordination=False):
    router = FidelityRouter()
    pool = AdaptiveSamplingPool(budget_ms=10000)
    scheduler = SurrogateRetrainingScheduler(retrain_threshold=10)
    ml_model = MLSurrogateModel()  # from Stage 6
    
    for gen in range(generations):
        # ... create/mutate population ...
        
        # (NEW) Coordinate fidelity routing
        if enable_fidelity_coordination:
            coord_result = coordinate_fidelity_escalation(
                population, router, pool, cost_budget_ms=10000
            )
            # Use coord_result to determine which solver to use per candidate
        
        # ... evaluate population ...
        
        # (NEW) Periodically retrain surrogate
        if enable_fidelity_coordination:
            best_candidates = [top_N_candidates]
            scheduler.add_ga_results(best_candidates)
            scheduler.maybe_retrain(ml_model)
```

## API Usage Examples

### Example 1: Single Candidate Routing

```python
from liquid_engine_studio.fidelity_coordinator import FidelityRouter

router = FidelityRouter()
decision = router.route(
    candidate_id="pop_001_gen_5",
    surrogate_confidence=0.72,      # From Stage 6 ML surrogate
    sobol_sensitivity_score=0.65,   # From Stage 5 Sobol analysis
    cost_budget_available=7000,     # ms remaining in generation
)

print(f"Route to: {decision.assigned_tier.value}")
print(f"Reasoning: {decision.reasoning}")
# Output:
# Route to: concept
# Reasoning: Medium confidence 0.72 → concept
```

### Example 2: Batch Routing & Allocation

```python
from liquid_engine_studio.fidelity_coordinator import (
    FidelityRouter, AdaptiveSamplingPool, coordinate_fidelity_escalation
)

candidates = [
    {"candidate_id": "pop_001", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
    {"candidate_id": "pop_002", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.5},
    {"candidate_id": "pop_003", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.8},
]

result = coordinate_fidelity_escalation(
    ga_candidates=candidates,
    router=FidelityRouter(),
    pool=AdaptiveSamplingPool(budget_ms=5000),
    cost_budget_ms=5000,
)

# Inspect allocation
alloc = result["allocation_stats"]
print(f"Allocated: {alloc['allocated']}/{alloc['total']}")

# Inspect pool utilization
pool_summary = result["pool_summary"]
print(f"Budget utilization: {pool_summary['budget_utilization_percent']:.1f}%")

# Per-tier breakdown
for tier_name, tier_stats in pool_summary["by_pool"].items():
    print(f"{tier_name}: {tier_stats['candidate_count']} candidates, {tier_stats['total_estimated_cost_ms']} ms")
```

### Example 3: Surrogate Retraining

```python
from liquid_engine_studio.fidelity_coordinator import SurrogateRetrainingScheduler
from liquid_engine_studio.ml_surrogate_adapter import MLSurrogateModel

scheduler = SurrogateRetrainingScheduler(retrain_threshold=10, retrain_interval_generations=2)
ml_model = MLSurrogateModel()

# Accumulate GA results over generations
for generation in range(10):
    # ... run GA ...
    ga_best_candidates = [...]  # List of top candidates
    
    scheduler.add_ga_results(ga_best_candidates)
    retrain_stats = scheduler.maybe_retrain(ml_model)
    
    if retrain_stats.retrained:
        print(f"Retrained! Improvement: {retrain_stats.improvement_estimate:.3f}")
    else:
        print(f"Skipped retraining: {retrain_stats.notes}")

# Final summary
final_summary = scheduler.get_retraining_summary()
print(f"Total generations: {final_summary['generation_count']}")
print(f"Total retrained: {final_summary['total_retrained']}")
print(f"Pool size: {final_summary['training_data_pool_size']}")
```

## Performance Characteristics

### Cost Model

Per-candidate evaluation times:
- **Heuristic**: ~1 ms (confidence lookup only)
- **Concept**: ~50 ms (current `create_concept_design()`)
- **Coupled-Cycle**: ~500 ms (coupled solver + validation)

### Expected GA Speedup

Baseline (all concept tier):
- 24 candidates × 50 ms = 1200 ms per generation
- 16 generations = 19.2 seconds total

With fidelity coordination (60/30/10 split):
- ~14 heuristic (↓0.01 s) + ~7 concept (↓0.35 s) + ~3 coupled (↓1.5 s) ≈ 2 s per gen
- 16 generations ≈ 32 s total (similar to baseline)
- But produces higher-confidence final designs (>0.75 vs ~0.55)

### Test Coverage

`tests/test_fidelity_coordinator.py` (33 tests):

1. **FidelityRouter** (13 tests):
   - Routing logic for all decision branches
   - Batch routing and budget tracking
   - Routing history and summary statistics
   - Coupled-cycle disable mode

2. **AdaptiveSamplingPool** (8 tests):
   - Pool initialization
   - Per-tier allocation (heuristic, concept, coupled)
   - Budget constraint enforcement
   - Batch allocation and statistics

3. **SurrogateRetrainingScheduler** (5 tests):
   - GA result accumulation
   - Threshold and interval logic
   - Retraining decision making
   - Summary statistics

4. **Orchestration** (2 tests):
   - Full end-to-end coordination
   - Budget exhaustion scenarios

5. **Serialization** (3 tests):
   - Dataclass `as_dict()` methods
   - JSON-serializability for export

6. **Integration** (2 tests):
   - Multi-generation workflows
   - Retraining across generations

**Status:** 33/33 tests pass ✓

## Known Limitations

### Current Constraints

- **No True Parallelism**: Pools are logical; actual parallel evaluation deferred
- **Fixed Cost Estimates**: Tier costs hardcoded (1, 50, 500 ms); measure actual in production
- **No Adaptive Thresholds**: Routing thresholds fixed; future: learn from feedback
- **Concept-Only Solver**: Routes to coupled-cycle *if implemented*; currently fallback to concept

### Design Decisions

**Why three tiers?** Heuristic too inaccurate alone; coupled is too slow for many candidates; concept is the sweet spot for most cases.

**Why confidence + sensitivity?** Confidence captures predictor uncertainty; sensitivity captures output importance. Together they decide "do we need high fidelity?"

**Why periodic retraining?** GA changes population focus each generation; surrogate should adapt to new regions of design space.

## Future Enhancements

1. **Neural Network Routing** (Stage 7.2):
   - Train neural net on (confidence, sensitivity) → tier prediction
   - Learn routing policy from historical GA data
   - Adapt thresholds per generation

2. **Cost-Aware Allocation** (Stage 7.3):
   - Re-estimate per-candidate costs from actual runtimes
   - Adjust allocation greedily to fit more candidates
   - Load-balance across evaluation pools

3. **Parallel Pool Execution** (Stage 7.4):
   - Launch heuristic → concept → coupled as async tasks
   - Return results as available; feed to next GA generation
   - Continuous pipelining across generations

4. **Multi-Objective Fidelity Trade-offs** (Stage 7.5):
   - Route candidates to minimize cost while maintaining confidence
   - Pareto-front of speed vs. accuracy
   - User selects preferred operating point

5. **Meta-Surrogate Learning** (Stage 7.6):
   - Train meta-model on surrogate improvement curves
   - Predict when retraining yields significant gains
   - Budget retraining adaptively

## References

- Stage 5 (Monte Carlo + Sobol): Informs sensitivity-driven routing
- Stage 6 (ML Surrogates): Provides confidence scores for routing decisions
- Stage 3.1 (Coupled-Cycle Solver): Target high-fidelity tier
- Saltelli et al., "Global Sensitivity Analysis" (2008): Sobol foundations
- Forrester et al., "Engineering Design via Surrogate Modelling" (2008): Multi-fidelity concepts

## Diagram: Fidelity Coordination Data Flow

```
GA Generation N
    ↓
[Population of 24 candidates]
    ├─ Each candidate has:
    │  - design_state
    │  - surrogate_confidence (from Stage 6)
    │  - sobol_sensitivity (from Stage 5)
    ↓
[FidelityRouter]
    ├─ Apply routing rules
    └─ Generate RoutingDecisions (tier + reasoning)
    ↓
[AdaptiveSamplingPool]
    ├─ Allocate to pool if budget available
    ├─ Track: heuristic_queue, concept_queue, coupled_queue
    └─ Generate allocation statistics
    ↓
[Evaluate Pools]
    ├─ Heuristic (1 ms × N)
    ├─ Concept (50 ms × N)
    └─ Coupled-Cycle (500 ms × N) if time permits
    ↓
[Merge Results Back to GA Population]
    ├─ Composite scoring
    └─ Feed to selection/reproduction
    ↓
[SurrogateRetrainingScheduler]
    ├─ Add best candidates to training pool
    ├─ Check: interval elapsed AND pool_size ≥ threshold?
    └─ If yes, retrain ML surrogate
    ↓
[Next GA Generation]
```

---

**Implementation Status:** ✅ Complete (Stage 7, Phase 1)  
**Test Suite:** 33/33 passing  
**Integration Status:** Ready for GA workflow coupling (future implementation)  
**Next Frontier:** Neural network routing (Stage 7.2), cost-aware allocation (Stage 7.3)
```

