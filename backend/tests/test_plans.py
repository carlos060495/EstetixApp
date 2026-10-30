"""Config de planes: límites de empleados y jerarquía de features.

Fuente de verdad ÚNICA en backend para el gating por plan. El plan real del
salón vive en salons.plan (fuente de verdad DESDE Stripe vía webhook).
"""

import pytest

from app.billing import plans


class TestEmployeeLimit:
    def test_limit_per_plan(self):
        """Los tres números, escritos. Son un REQUISITO de producto, no un
        detalle: cambiar uno cambia lo que un cliente puede hacer con lo que
        paga, y tiene que costar tocar un test."""
        assert plans.employee_limit("basico") == 4
        assert plans.employee_limit("pro") == 8
        assert plans.employee_limit("elite") == 16

    def test_unknown_plan_falls_back_to_most_restrictive(self):
        # Un plan desconocido nunca debe conceder más que el más restrictivo.
        assert plans.employee_limit("desconocido") == 4


class TestPlanAllows:
    @pytest.mark.parametrize(
        "current,minimum,expected",
        [
            ("basico", "basico", True),
            ("pro", "basico", True),
            ("elite", "basico", True),
            ("pro", "pro", True),
            ("elite", "pro", True),
            ("basico", "pro", False),
            ("basico", "elite", False),
            ("pro", "elite", False),
            ("elite", "elite", True),
        ],
    )
    def test_hierarchy(self, current, minimum, expected):
        assert plans.plan_allows(current, minimum) is expected

    def test_unknown_current_is_treated_as_basico(self):
        assert plans.plan_allows("desconocido", "pro") is False
        assert plans.plan_allows("desconocido", "basico") is True


class TestFeatureMinPlan:
    def test_premium_features_require_expected_plan(self):
        assert plans.FEATURE_MIN_PLAN["asistente"] == "pro"
        assert plans.FEATURE_MIN_PLAN["conversaciones"] == "pro"
        assert plans.FEATURE_MIN_PLAN["insights"] == "elite"

    def test_las_features_retiradas_ya_no_estan(self):
        """Marketing y Fidelización se anunciaban en los planes sin existir. Se
        retiraron enteras, no se aplazaron: si alguna vuelve al diccionario sin
        pantalla detrás, vuelve el mismo problema."""
        assert "marketing" not in plans.FEATURE_MIN_PLAN
        assert "fidelizacion" not in plans.FEATURE_MIN_PLAN
