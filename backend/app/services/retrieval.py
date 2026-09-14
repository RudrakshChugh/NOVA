"""pgvector similarity search over a course's material chunks."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Chunk, Material
from .embeddings import embed


def search(db: Session, course_id: UUID, query: str, top_k: int) -> list[tuple[Chunk, float]]:
    """Returns (chunk, cosine_similarity) ordered most similar first."""
    qvec = embed([query])[0]
    distance = Chunk.embedding.cosine_distance(qvec)
    rows = db.execute(
        select(Chunk, distance)
        .join(Material, Material.material_id == Chunk.material_id)
        .where(Material.course_id == course_id)
        .order_by(distance)
        .limit(top_k)
    ).all()
    return [(chunk, 1.0 - dist) for chunk, dist in rows]


def similarity(a: str, b: str) -> float:
    va, vb = embed([a, b])
    return float(sum(x * y for x, y in zip(va, vb)))  # vectors are L2-normalised
