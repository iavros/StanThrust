# Optimization Adapter

## Purpose

Connect the solver pipeline to the genetic algorithm without coupling the optimizer to UI state or renderer details.

## What it should do

- Convert normalized requests into optimization genomes
- Decode genomes back into solver-ready requests
- Evaluate candidates through the solver stack
- Aggregate objective scores
- Return optimizer history and best-candidate bundles

## Primary handles

Handle: `build_genome_seed(design_request, objective_weights) -> genome_seed`

- Inputs:
  - normalized request
  - objective weights
- Outputs:
  - `genome_template`
  - `bounds`
  - `constraints`
  - `objective_weights`

Handle: `decode_genome(genome_seed, genome) -> candidate_request`

- Inputs:
  - seed
  - genome
- Outputs:
  - solver-ready candidate request

Handle: `evaluate_candidate(candidate_request) -> candidate_result`

- Inputs:
  - candidate request
- Outputs:
  - solver outputs
  - objective scores
  - warnings

Handle: `run_optimizer(genome_seed, options) -> optimization_result`

- Inputs:
  - seed
  - generation count
  - population size
  - random seed
- Outputs:
  - best candidate
  - score history
  - best solver bundles

## Important coupling rules

- The optimizer should call solver adapters, not UI widgets.
- Objective functions should score solver outputs, not raw user inputs directly.
