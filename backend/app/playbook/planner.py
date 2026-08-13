"""Deterministic Playbook planner with safe condition evaluation."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from app.exceptions import PlaybookValidationError
from app.playbook.contracts import PlaybookDocument, PlaybookStepDefinition
from app.playbook.policy import PlaybookPolicy


@dataclass(frozen=True, slots=True)
class PlaybookPlan:
    document: PlaybookDocument
    steps: tuple[PlaybookStepDefinition, ...]


class SafeConditionEvaluator:
    """Evaluate a small expression language without eval, calls, or attribute access."""

    _comparison_ops = {
        ast.Eq: lambda left, right: left == right,
        ast.NotEq: lambda left, right: left != right,
        ast.In: lambda left, right: left in right,
        ast.NotIn: lambda left, right: left not in right,
        ast.Gt: lambda left, right: left > right,
        ast.GtE: lambda left, right: left >= right,
        ast.Lt: lambda left, right: left < right,
        ast.LtE: lambda left, right: left <= right,
    }

    def evaluate(self, expression: str, context: dict[str, Any]) -> bool:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as error:
            raise PlaybookValidationError("Invalid condition syntax") from error
        return bool(self._visit(tree.body, context))

    def _visit(self, node: ast.AST, context: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in context:
                raise PlaybookValidationError(f"Unknown condition variable: {node.id}")
            return context[node.id]
        if isinstance(node, ast.Subscript):
            container = self._visit(node.value, context)
            key = self._visit(node.slice, context)
            if not isinstance(container, (dict, list, tuple)):
                raise PlaybookValidationError("Condition subscript requires a mapping or sequence")
            try:
                return container[key]
            except (KeyError, IndexError, TypeError) as error:
                raise PlaybookValidationError("Condition subscript is not available") from error
        if isinstance(node, ast.List):
            return [self._visit(item, context) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._visit(item, context) for item in node.elts)
        if isinstance(node, ast.Dict):
            return {
                self._visit(key, context): self._visit(value, context)
                for key, value in zip(node.keys, node.values, strict=True)
            }
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not bool(self._visit(node.operand, context))
        if isinstance(node, ast.BoolOp):
            values = [bool(self._visit(value, context)) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare):
            left = self._visit(node.left, context)
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                right = self._visit(comparator, context)
                operation = self._comparison_ops.get(type(operator))
                if operation is None or not operation(left, right):
                    return False
                left = right
            return True
        raise PlaybookValidationError(f"Condition operation is not allowed: {type(node).__name__}")


class PlaybookPlanner:
    def __init__(self, policy: PlaybookPolicy) -> None:
        self._policy = policy

    def plan(self, document: PlaybookDocument, *, actor: str) -> PlaybookPlan:
        self._policy.validate_document(document)
        self._policy.authorize_runner(actor)
        if document.allowed_runners and actor not in document.allowed_runners:
            raise PlaybookValidationError("Runner is not allowed by this Playbook")
        return PlaybookPlan(document=document, steps=tuple(document.steps))
