from dataclasses import asdict, dataclass

from devpilot.config import ModelPrice


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def cost(self, price: ModelPrice) -> float:
        return (
            self.input_tokens * price.input
            + self.output_tokens * price.output
            + self.cache_read_tokens * price.cache_read
            + self.cache_write_tokens * price.cache_write
        ) / 1_000_000

    def to_dict(self) -> dict[str, int]:
        return asdict(self)
