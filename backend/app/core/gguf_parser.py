"""
Mustang AI GGUF Binary Metadata & Quantization Parser
Safely extracts architecture metadata, parameter counts, tensor types, and GQA layout.
"""

import logging
import math
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
import gguf

logger = logging.getLogger("mustang.gguf_parser")

GGUF_FILE_TYPE_MAP: dict[int, str] = {
    0: "ALL_F32",
    1: "MOSTLY_F16",
    2: "MOSTLY_Q4_0",
    3: "MOSTLY_Q4_1",
    7: "MOSTLY_Q8_0",
    8: "MOSTLY_Q5_0",
    9: "MOSTLY_Q5_1",
    10: "MOSTLY_Q2_K",
    11: "MOSTLY_Q3_K_S",
    12: "MOSTLY_Q3_K_M",
    13: "MOSTLY_Q3_K_L",
    14: "MOSTLY_Q4_K_S",
    15: "MOSTLY_Q4_K_M",
    16: "MOSTLY_Q5_K_S",
    17: "MOSTLY_Q5_K_M",
    18: "MOSTLY_Q6_K",
    19: "MOSTLY_IQ2_XXS",
    20: "MOSTLY_IQ2_XS",
    21: "MOSTLY_Q2_K_S",
    22: "MOSTLY_IQ3_XS",
    23: "MOSTLY_IQ3_XXS",
    24: "MOSTLY_IQ1_S",
    25: "MOSTLY_IQ4_NL",
    26: "MOSTLY_IQ3_S",
    27: "MOSTLY_IQ3_M",
    28: "MOSTLY_IQ2_S",
    29: "MOSTLY_IQ2_M",
    30: "MOSTLY_IQ4_XS",
    31: "MOSTLY_IQ1_M",
    32: "MOSTLY_BF16",
    33: "MOSTLY_Q4_0_4_4",
    34: "MOSTLY_Q4_0_4_8",
    35: "MOSTLY_Q4_0_8_8",
    36: "MOSTLY_TQ1_0",
    37: "MOSTLY_TQ2_0",
}


def clean_quantization_name(file_type_id: int, fallback_filename: str = "") -> str:
    """Converts GGUF file_type ID to clean quantization tag (e.g. Q4_K_M)."""
    raw = GGUF_FILE_TYPE_MAP.get(file_type_id)
    if raw:
        if raw.startswith("MOSTLY_"):
            return raw[7:]
        return raw

    # Fallback to inspecting filename if file_type not in map
    upper = fallback_filename.upper()
    for q in [
        "Q4_K_M", "Q4_K_S", "Q5_K_M", "Q5_K_S", "Q8_0", "Q4_0", "Q4_1",
        "Q6_K", "Q2_K", "Q3_K_M", "Q3_K_S", "Q3_K_L", "BF16", "F16", "F32",
    ]:
        if q in upper:
            return q
    return "UNKNOWN"


# Architectures mathematically verified to use symmetric Key and Value projections (D_K == D_V).
# In these architectures, missing value_length safely falls back to key_length,
# and head_dim or embedding_dim // head_count safely applies to both K and V.
VERIFIED_SYMMETRIC_ARCHITECTURES: set[str] = {
    "llama",
    "qwen2",
    "qwen",
    "mistral",
    "gemma",
    "gemma2",
    "phi3",
    "phi",
    "starcoder",
    "starcoder2",
    "command-r",
    "baichuan",
}

# Architectures explicitly known to use unsupported or non-linear asymmetric KV representations
UNSUPPORTED_ASYMMETRIC_ARCHITECTURES: set[str] = {
    "deepseek2",
    "deepseek3",
    "mla",
    "unsupported_asym",
}


def is_architecture_verified_symmetric(arch: str) -> bool:
    """Returns True if architecture is mathematically proven to have symmetric Key/Value dimensions."""
    arch_lower = (arch or "").lower()
    if any(k in arch_lower for k in ("deepseek2", "deepseek3", "mla", "unsupported_asym")):
        return False
    return (
        arch_lower in VERIFIED_SYMMETRIC_ARCHITECTURES
        or any(k in arch_lower for k in ("llama", "qwen", "mistral", "gemma", "starcoder", "phi", "command-r", "baichuan"))
    )


def resolve_head_dimensions(
    architecture: str,
    key_length: int = 0,
    value_length: int = 0,
    head_dim: int = 0,
    embedding_dim: int = 0,
    head_count: int = 0,
) -> tuple[int, int, bool, str, str | None]:
    """
    Resolves Key and Value head dimensions with strict architecture validation.
    Returns:
        (dim_k, dim_v, head_dim_derived, status, error_reason)
        where status is 'VALIDATED', 'EXPLICIT_ASYMMETRIC', or 'UNAVAILABLE'.
    """
    arch_lower = (architecture or "").lower()
    raw_key_len = int(key_length or 0)
    raw_val_len = int(value_length or 0)
    raw_head_dim = int(head_dim or 0)

    is_symmetric_arch = is_architecture_verified_symmetric(arch_lower)
    is_unsupported_asym = (
        arch_lower in UNSUPPORTED_ASYMMETRIC_ARCHITECTURES
        or any(k in arch_lower for k in ("deepseek2", "deepseek3", "mla", "unsupported_asym"))
    )

    # Case 1: Both Key and Value dimensions explicitly provided in metadata
    if raw_key_len > 0 and raw_val_len > 0:
        if raw_key_len != raw_val_len:
            if is_unsupported_asym:
                return (
                    0, 0, False, "UNAVAILABLE",
                    f"Architecture '{architecture}' uses asymmetric Key/Value dimensions (D_K={raw_key_len}, D_V={raw_val_len}) which is not supported for linear KV cache estimation.",
                )
            # Supported explicit asymmetric dimensions (e.g. verified linear model with decoupled projections)
            return (raw_key_len, raw_val_len, False, "EXPLICIT_ASYMMETRIC", None)
        else:
            return (raw_key_len, raw_val_len, False, "VALIDATED", None)

    # Case 2: Explicit Key dimension provided, but Value dimension missing
    if raw_key_len > 0 and raw_val_len <= 0:
        if is_symmetric_arch:
            return (raw_key_len, raw_key_len, False, "VALIDATED", None)
        else:
            return (
                0, 0, False, "UNAVAILABLE",
                f"Architecture '{architecture}' metadata provides explicit key dimension ({raw_key_len}) but missing value_length, and architecture is not validated as symmetric.",
            )

    # Case 3: Explicit Value dimension provided, but Key dimension missing
    if raw_val_len > 0 and raw_key_len <= 0:
        if is_symmetric_arch:
            return (raw_val_len, raw_val_len, False, "VALIDATED", None)
        else:
            return (
                0, 0, False, "UNAVAILABLE",
                f"Architecture '{architecture}' metadata provides explicit value dimension ({raw_val_len}) but missing key_length, and architecture is not validated as symmetric.",
            )

    # Case 4: Neither Key nor Value specified, but explicit head_dim provided
    if raw_head_dim > 0:
        if is_symmetric_arch:
            return (raw_head_dim, raw_head_dim, False, "VALIDATED", None)
        else:
            return (
                0, 0, False, "UNAVAILABLE",
                f"Architecture '{architecture}' metadata specifies head_dim={raw_head_dim} without independent Key/Value dimensions, and architecture is not validated as symmetric.",
            )

    # Case 5: Derived from embedding_dim // head_count
    if embedding_dim > 0 and head_count > 0:
        if is_symmetric_arch:
            shared_dim = embedding_dim // head_count
            return (shared_dim, shared_dim, True, "VALIDATED", None)
        else:
            return (
                0, 0, False, "UNAVAILABLE",
                f"Architecture '{architecture}' attention head dimensions cannot be established: deriving from embedding_dim // head_count requires verified symmetric architecture validation.",
            )

    # Case 6: Cannot be established
    return (
        0, 0, False, "UNAVAILABLE",
        f"Architecture '{architecture}' attention head dimensions cannot be established from metadata.",
    )


class GGUFMetadata(BaseModel):
    architecture: str
    parameter_count: str | None = None
    block_count: int = Field(ge=0, description="Transformer layer count")
    total_layers: int = Field(ge=0, description="block_count + 1 output layer")
    context_length: int = Field(ge=0, description="Metadata context limit")
    embedding_dim: int = Field(ge=0)
    feed_forward_length: int | None = None
    head_count: int = Field(ge=1)
    head_count_kv: int = Field(ge=1)
    key_length: int | None = None
    value_length: int | None = None
    head_dim: int = Field(default=0, ge=0, description="Explicit key_length or derived embedding_dim // head_count")
    head_dim_derived: bool = Field(default=False, description="True if head_dim is derived rather than explicit in GGUF metadata")
    sliding_window: int | None = Field(default=None, description="Sliding window attention size if applicable")
    attention_type: Literal["MHA", "GQA", "MQA", "UNKNOWN"]
    quantization: str
    file_type_id: int
    tensor_count: int
    file_size_bytes: int


def _format_param_count(total_params: int) -> str:
    if total_params >= 1_000_000_000:
        return f"{total_params / 1_000_000_000:.1f}B"
    elif total_params >= 100_000_000:
        return f"{total_params / 1_000_000_000:.2f}B"
    elif total_params >= 1_000_000:
        return f"{total_params / 1_000_000:.0f}M"
    return str(total_params)


def parse_gguf_metadata(file_path: Path | str) -> GGUFMetadata:
    """
    Parses a local GGUF file header and returns strongly typed model metadata.
    Enforces GGUF magic bytes check, architecture detection, and GQA layout.
    """
    target = Path(file_path).resolve()
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Model file does not exist: {target}")

    # Validate GGUF Magic bytes (first 4 bytes must be b'GGUF')
    with open(target, "rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            raise ValueError(f"Invalid GGUF file: expected magic b'GGUF', got {magic!r}")

    reader = gguf.GGUFReader(str(target))

    # Architecture string
    arch_field = reader.get_field("general.architecture")
    if not arch_field:
        arch_str = "unknown"
    else:
        part = arch_field.parts[-1]
        arch_str = str(bytes(part), "utf-8", errors="replace")

    def get_val(key_suffix: str, default=0):
        # First check with architecture prefix, then without
        field = reader.get_field(f"{arch_str}.{key_suffix}") or reader.get_field(f"general.{key_suffix}")
        if field and len(field.parts) > 0:
            val = field.parts[-1]
            if hasattr(val, "tolist"):
                val = val.tolist()
            if isinstance(val, list) and len(val) > 0:
                return val[0]
            try:
                return int(val)
            except Exception:
                return val
        return default

    block_count = int(get_val("block_count", 0))
    context_length = int(get_val("context_length", 4096))
    embedding_dim = int(get_val("embedding_length", 0))
    feed_forward = int(get_val("feed_forward_length", 0)) or None
    head_count = int(get_val("attention.head_count", 0))
    head_count_kv = int(get_val("attention.head_count_kv", 0))
    raw_head_dim = int(get_val("attention.head_dim", 0)) or None
    key_length = int(get_val("attention.key_length", 0)) or None
    value_length = int(get_val("attention.value_length", 0)) or None
    sliding_window = int(get_val("attention.sliding_window", 0)) or None

    # Derive or extract head dimension with architecture validation
    dim_k, dim_v, head_dim_derived, status, _ = resolve_head_dimensions(
        architecture=arch_str,
        key_length=key_length or 0,
        value_length=value_length or 0,
        head_dim=raw_head_dim or 0,
        embedding_dim=embedding_dim,
        head_count=head_count,
    )
    if status != "UNAVAILABLE":
        head_dim = dim_k
    else:
        head_dim = key_length or value_length or raw_head_dim or (embedding_dim // head_count if head_count > 0 else 0)
        head_dim_derived = True

    # If head_count_kv is not specified or 0, it defaults to head_count (standard MHA)
    if head_count_kv <= 0:
        head_count_kv = head_count

    # Determine attention layout
    if head_count == 0:
        attention_type = "UNKNOWN"
    elif head_count_kv == 1:
        attention_type = "MQA"  # Multi-Query Attention
    elif head_count_kv < head_count:
        attention_type = "GQA"  # Grouped Query Attention
    else:
        attention_type = "MHA"  # Multi-Head Attention

    # Quantization / file_type
    file_type_id = int(get_val("file_type", 0))
    quantization = clean_quantization_name(file_type_id, target.name)

    # Calculate parameter count from tensors if general.parameter_count not present
    param_field = reader.get_field("general.parameter_count")
    if param_field:
        raw_p = int(param_field.parts[-1])
        parameter_count = _format_param_count(raw_p)
    else:
        try:
            total_params = sum(int(math.prod(t.shape)) for t in reader.tensors)
            parameter_count = _format_param_count(total_params) if total_params > 0 else None
        except Exception:
            parameter_count = None

    tensor_count = len(reader.tensors)
    file_size_bytes = target.stat().st_size
    total_layers = block_count + 1 if block_count > 0 else 0

    return GGUFMetadata(
        architecture=arch_str,
        parameter_count=parameter_count,
        block_count=block_count,
        total_layers=total_layers,
        context_length=context_length,
        embedding_dim=embedding_dim,
        feed_forward_length=feed_forward,
        head_count=max(head_count, 1),
        head_count_kv=max(head_count_kv, 1),
        key_length=key_length,
        value_length=value_length,
        head_dim=head_dim,
        head_dim_derived=head_dim_derived,
        sliding_window=sliding_window,
        attention_type=attention_type,
        quantization=quantization,
        file_type_id=file_type_id,
        tensor_count=tensor_count,
        file_size_bytes=file_size_bytes,
    )
