# ADK Automated Agent Evaluations

This directory stores deterministic trajectory and response quality benchmarks evaluated via Google ADK's `adk eval` runner.

## Running Locally

```bash
export GOOGLE_API_KEY="your_gemini_api_key"
adk eval lease_negotiator evals/lease_negotiation.test.json \
  --config_file_path evals/eval_config.json \
  --print_detailed_results
```

## Metrics Configured (`eval_config.json`)

- **`tool_trajectory_avg_score` (`>= 0.80`)**: Verifies the Coordinator/Auditor selects the right strict Pydantic lease tools with accurate extracted worksheet numbers.
- **`response_match_score` (`>= 0.55`)**: Verifies final guidance flags dealer markups, CA `$85` doc-fee limits, VCFS refundable MSD rules, and actionable next steps.
