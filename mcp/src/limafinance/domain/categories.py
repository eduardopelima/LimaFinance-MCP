import re
from dataclasses import dataclass
from typing import Literal

from ..db import DB
from ..lib.errors import DomainError
from ..lib.ids import new_id
from ..lib.text import levenshtein, normalize_key
from .context import Actor, audit_stmt

Kind = Literal["income", "expense"]


@dataclass(frozen=True)
class CategoryRow:
    id: str
    parent_id: str | None
    name: str
    name_key: str
    kind: Kind
    archived_at: str | None = None


@dataclass(frozen=True)
class ResolvedCategory:
    id: str
    kind: Kind
    path: str  # "Alimentação > Mercado"
    root_id: str


async def load_categories(db: DB, user_id: str) -> list[CategoryRow]:
    rows = await db.all(
        "SELECT id, parent_id, name, name_key, kind, archived_at FROM categories WHERE user_id = ? ORDER BY kind, name_key",
        user_id,
    )
    return [CategoryRow(**r) for r in rows]


def category_path(c: CategoryRow, by_id: dict[str, CategoryRow]) -> str:
    parent = by_id.get(c.parent_id) if c.parent_id else None
    return f"{parent.name} > {c.name}" if parent else c.name


def category_tree(rows: list[CategoryRow]) -> list[dict]:
    active = [r for r in rows if not r.archived_at]
    return [
        {
            "id": root.id,
            "name": root.name,
            "kind": root.kind,
            "subcategories": [{"id": s.id, "name": s.name} for s in active if s.parent_id == root.id],
        }
        for root in active
        if not root.parent_id
    ]


def resolve_category(rows: list[CategoryRow], ref: str, kind: Kind | None = None) -> ResolvedCategory:
    """Resolve referência de categoria SEM criar nada. Erro estrito se não existir.

    Formatos: "cat_..." | "Alimentação" | "Alimentação > Mercado" | "Alimentação/Mercado" |
    "Mercado" (se a subcategoria for única). Ignora maiúsculas, acentos e espaços extras.
    """
    by_id = {r.id: r for r in rows}
    active = [r for r in rows if not r.archived_at and (kind is None or r.kind == kind)]

    def resolved(c: CategoryRow) -> ResolvedCategory:
        return ResolvedCategory(c.id, c.kind, category_path(c, by_id), c.parent_id or c.id)

    ref_s = ref.strip()
    if ref_s.startswith("cat_"):
        c = by_id.get(ref_s)
        if not c or c.archived_at:
            raise _not_found(ref, active, by_id, kind)
        if kind and c.kind != kind:
            raise _kind_mismatch(c, by_id, kind)
        return resolved(c)

    parts = [normalize_key(p) for p in re.split(r"\s*[>/]\s*", ref_s) if normalize_key(p)]
    if not parts or len(parts) > 2:
        raise _not_found(ref, active, by_id, kind)

    if len(parts) == 2:
        root = next((r for r in active if not r.parent_id and r.name_key == parts[0]), None)
        sub = root and next((r for r in active if r.parent_id == root.id and r.name_key == parts[1]), None)
        if sub:
            return resolved(sub)
        raise _not_found(ref, active, by_id, kind)

    key = parts[0]
    root = next((r for r in active if not r.parent_id and r.name_key == key), None)
    if root:
        return resolved(root)
    subs = [r for r in active if r.parent_id and r.name_key == key]
    if len(subs) == 1:
        return resolved(subs[0])
    if len(subs) > 1:
        options = " ou ".join(f'"{category_path(s, by_id)}"' for s in subs)
        raise DomainError("ambiguous_category", f'A subcategoria "{ref}" existe em mais de uma categoria. Especifique: {options}.')
    if kind:
        other = next((r for r in rows if not r.archived_at and r.name_key == key and r.kind != kind), None)
        if other:
            raise _kind_mismatch(other, by_id, kind)
    raise _not_found(ref, active, by_id, kind)


def _label(kind: Kind) -> str:
    return "ENTRADA" if kind == "income" else "SAÍDA"


def _kind_mismatch(c: CategoryRow, by_id: dict[str, CategoryRow], kind: Kind) -> DomainError:
    return DomainError(
        "category_kind_mismatch",
        f'A categoria "{category_path(c, by_id)}" é de {_label(c.kind)}, mas o lançamento é de {_label(kind)}.',
    )


def _not_found(ref: str, candidates: list[CategoryRow], by_id: dict[str, CategoryRow], kind: Kind | None) -> DomainError:
    key = normalize_key(re.split(r"[>/]", ref)[-1])
    threshold = max(2, len(key) // 3)
    scored = sorted(((levenshtein(key, c.name_key), c) for c in candidates), key=lambda x: x[0])
    suggestions = [category_path(c, by_id) for d, c in scored if d <= threshold][:3]
    where = "" if kind is None else (" para entradas" if kind == "income" else " para saídas")
    msg = f'Categoria "{ref}" não existe{where}. '
    if suggestions:
        msg += "Você quis dizer: " + ", ".join(f'"{s}"' for s in suggestions) + "? "
    msg += "Não crio categorias automaticamente: confirme com o usuário e use create_category se ele quiser uma nova."
    return DomainError(
        "category_not_found",
        msg,
        {"suggestions": suggestions, "available": [category_path(c, by_id) for c in candidates]},
    )


async def create_category(db: DB, actor: Actor, name: str, parent: str | None = None, kind: Kind | None = None) -> dict:
    name = re.sub(r"\s+", " ", name.strip())
    if not name or re.search(r"[>/]", name):
        raise DomainError("invalid_name", 'Nome de categoria não pode ser vazio nem conter ">" ou "/".')
    rows = await load_categories(db, actor.user_id)
    by_id = {r.id: r for r in rows}
    parent_row = None
    if parent:
        parent_row = by_id[resolve_category(rows, parent).id]
        if parent_row.parent_id:
            raise DomainError("too_deep", "Só existem 2 níveis: categoria > subcategoria.")
        if kind and kind != parent_row.kind:
            raise DomainError("category_kind_mismatch", f"Subcategoria herda o tipo da categoria-mãe ({parent_row.kind}).")
    elif not kind:
        raise DomainError("kind_required", 'Informe kind ("income" ou "expense") para uma categoria raiz.')
    final_kind = parent_row.kind if parent_row else kind
    name_key = normalize_key(name)
    parent_key = parent_row.id if parent_row else ""
    existing = next((r for r in rows if (r.parent_id or "") == parent_key and r.name_key == name_key), None)
    if existing:
        raise DomainError("category_exists", f'A categoria "{category_path(existing, by_id)}" já existe.', {"id": existing.id})
    cid = new_id("cat")
    await db.batch([
        db.stmt(
            "INSERT INTO categories (id, user_id, parent_id, parent_key, name, name_key, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
            cid, actor.user_id, parent_row.id if parent_row else None, parent_key, name, name_key, final_kind,
        ),
        audit_stmt(db, actor, "category.create", cid, {"name": name, "parent": parent_row.name if parent_row else None, "kind": final_kind}),
    ])
    return {"id": cid, "name": name, "kind": final_kind, "path": f"{parent_row.name} > {name}" if parent_row else name}
