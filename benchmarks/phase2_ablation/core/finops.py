import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

logger = logging.getLogger("MESA_FinOps")


@dataclass
class UsageMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cached_tokens: int = 0  # for OpenAI specific cached tokens


@dataclass
class ModelCostRates:
    # Rates represent cost per single token in USD
    input_token_cost: float
    output_token_cost: float
    cache_creation_input_token_cost: float = 0.0
    cache_read_input_token_cost: float = 0.0
    cached_token_cost: float = 0.0


# Dynamic pricing model (prices converted from per 1M to per-token rates)
MODEL_RATES = {
    # Frontier Models (Expensive)
    "claude-3-5-sonnet-20240620": ModelCostRates(
        input_token_cost=3.0 / 1e6,
        output_token_cost=15.0 / 1e6,
        cache_creation_input_token_cost=3.75 / 1e6,
        cache_read_input_token_cost=0.30 / 1e6,
    ),
    "gpt-4o": ModelCostRates(
        input_token_cost=5.0 / 1e6,
        output_token_cost=15.0 / 1e6,
        cached_token_cost=2.5 / 1e6,
    ),
    # Cheap/Local Routing Models
    "claude-3-haiku-20240307": ModelCostRates(
        input_token_cost=0.25 / 1e6,
        output_token_cost=1.25 / 1e6,
        cache_creation_input_token_cost=0.30 / 1e6,
        cache_read_input_token_cost=0.03 / 1e6,
    ),
    "gpt-4o-mini": ModelCostRates(
        input_token_cost=0.15 / 1e6,
        output_token_cost=0.60 / 1e6,
        cached_token_cost=0.075 / 1e6,
    ),
    "llama-3.1-8b-instant": ModelCostRates(
        input_token_cost=0.05 / 1e6,
        output_token_cost=0.05 / 1e6,
    ),
    "sentence-transformers/all-MiniLM-L6-v2": ModelCostRates(
        0, 0, 0, 0, 0
    ),  # Zero cost local
}


class FinOpsTracker:
    """Comprehensive cost-tracking module for MESA FinOps evaluation."""

    def __init__(self) -> None:
        self.usage_history: List[Tuple[str, str, UsageMetrics]] = (
            []
        )  # (query_id, model, metrics)
        self.total_invocations = 0
        self.adaptive_router_stats = {
            "trivial_queries_routed_locally": 0,
            "complex_queries_routed_frontier": 0,
            "cost_saved_usd": 0.0,
        }

    def record_usage(
        self, query_id: str, model: str, usage: UsageMetrics, is_trivial: bool = False
    ) -> None:
        """Track LLM API Invocation and token usage."""
        self.usage_history.append((query_id, model, usage))
        self.total_invocations += 1

        # Evaluate AdaptiveRouter
        # Proves mathematically if MESA's cascade routing successfully flags trivial queries early
        if is_trivial:
            self.adaptive_router_stats["trivial_queries_routed_locally"] += 1
            # Calculate what it would have cost if routed to a frontier model (e.g. gpt-4o)
            actual_cost = self.calculate_exact_cost(model, usage)
            frontier_cost = self.calculate_exact_cost("gpt-4o", usage)
            self.adaptive_router_stats["cost_saved_usd"] += frontier_cost - actual_cost
        else:
            self.adaptive_router_stats["complex_queries_routed_frontier"] += 1

    def calculate_exact_cost(self, model: str, usage: UsageMetrics) -> float:
        """Calculate exact costs by accounting for Prompt Caching discounts in USD."""
        rates = MODEL_RATES.get(model)
        if not rates:
            logger.warning(f"Cost rates not found for model: {model}, defaulting to 0.")
            return 0.0

        cost = 0.0
        # Base input and output token consumption
        cost += usage.input_tokens * rates.input_token_cost
        cost += usage.output_tokens * rates.output_token_cost

        # Anthropic caching structure logic
        cost += (
            usage.cache_creation_input_tokens * rates.cache_creation_input_token_cost
        )
        cost += usage.cache_read_input_tokens * rates.cache_read_input_token_cost

        # OpenAI caching structure logic
        cost += usage.cached_tokens * rates.cached_token_cost

        return cost

    def get_total_cost(self) -> float:
        """Calculate total cumulative cost in USD."""
        return sum(
            self.calculate_exact_cost(model, usage)
            for _, model, usage in self.usage_history
        )

    def get_finops_report(self) -> Dict[str, float | int]:
        """Generate a complete financial tracking report for the pipeline run."""
        return {
            "total_invocations": self.total_invocations,
            "total_cost_usd": round(self.get_total_cost(), 6),
            "trivial_queries": self.adaptive_router_stats[
                "trivial_queries_routed_locally"
            ],
            "complex_queries": self.adaptive_router_stats[
                "complex_queries_routed_frontier"
            ],
            "adaptive_router_savings_usd": round(
                self.adaptive_router_stats["cost_saved_usd"], 6
            ),
        }
