# Copyright 2010 NaN Projectes de Programari Lliure, S.L.
# Copyright 2014 Serv. Tec. Avanzados - Pedro M. Baeza
# Copyright 2014 Oihane Crucelaegui - AvanzOSC
# Copyright 2017 ForgeFlow S.L.
# Copyright 2017 Simone Rubino - Agile Business Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, exceptions, fields, models
from odoo.tools import formatLang


class QcInspection(models.Model):
    _name = "qc.inspection"
    _description = "Contrôle qualité"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    @api.depends("inspection_lines", "inspection_lines.success")
    def _compute_success(self):
        for i in self:
            i.success = all([x.success for x in i.inspection_lines])

    def object_selection_values(self):
        """
        Overridable method for adding more object models to an inspection.
        :return: A list with the selection's possible values.
        """
        return [("product.product", "Produit")]

    @api.depends("object_id")
    def _compute_product_id(self):
        for i in self:
            if i.object_id and i.object_id._name == "product.product":
                i.product_id = i.object_id
            else:
                i.product_id = False

    name = fields.Char(
        string="Numéro de contrôle",
        required=True,
        default="/",
        readonly=True,
        states={"draft": [("readonly", False)]},
        copy=False,
    )
    date = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        default=fields.Datetime.now,
        states={"draft": [("readonly", False)]},
    )
    object_id = fields.Reference(
        string="Référence",
        selection="object_selection_values",
        readonly=True,
        states={"draft": [("readonly", False)]},
        ondelete="set null",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        compute="_compute_product_id",
        store=True,
        help="Produit associé à l'inspection",
    )
    qty = fields.Float(string="Qté", default=1.0)
    test = fields.Many2one(comodel_name="qc.test", readonly=True)
    inspection_lines = fields.One2many(
        comodel_name="qc.inspection.line",
        inverse_name="inspection_id",
        readonly=True,
        states={"ready": [("readonly", False)]},
    )
    internal_notes = fields.Text(string="Notes internes")
    external_notes = fields.Text(
        states={"success": [("readonly", True)], "failed": [("readonly", True)]},
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("ready", "Prêt"),
            ("waiting", "En attente de l'approbation du superviseur"),
            ("success", "Succès de qualité"),
            ("failed", "La qualité a échoué"),
            ("canceled", "Annulé"),
        ],
        readonly=True,
        default="draft",
        tracking=True,
    )
    success = fields.Boolean(
        compute="_compute_success",
        help="Ce champ sera marqué si tous les tests ont réussi.",
        store=True,
    )
    auto_generated = fields.Boolean(
        string="Auto-generated",
        readonly=True,
        copy=False,
        help="Si une inspection est générée automatiquement, elle peut être annulée mais pas supprimée.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        readonly=True,
        states={"draft": [("readonly", False)]},
        default=lambda self: self.env.company,
    )
    user = fields.Many2one(
        comodel_name="res.users",
        string="Responsable",
        tracking=True,
        default=lambda self: self.env.user,
    )

    @api.model_create_multi
    def create(self, val_list):
        for vals in val_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("qc.inspection")
        return super().create(vals)

    def unlink(self):
        for inspection in self:
            if inspection.auto_generated:
                raise exceptions.UserError(
                    _("Vous ne pouvez pas supprimer une inspection générée automatiquement.")
                )
            if inspection.state != "draft":
                raise exceptions.UserError(
                    _("Vous ne pouvez pas supprimer une inspection qui n'est pas à l'état de brouillon.")
                )
        return super().unlink()

    def action_draft(self):
        self.write({"state": "draft"})

    def action_todo(self):
        for inspection in self:
            if not inspection.test:
                raise exceptions.UserError(_("Vous devez d'abord définir le test à effectuer."))
        self.write({"state": "ready"})

    def action_confirm(self):
        for inspection in self:
            for line in inspection.inspection_lines:
                if line.question_type == "qualitative" and not line.qualitative_value:
                    raise exceptions.UserError(
                        _(
                            "Vous devez fournir une réponse pour toutes "
                            "les questions qualitatives."
                        )
                    )
                elif line.question_type != "qualitative" and not line.uom_id:
                    raise exceptions.UserError(
                        _(
                            "Vous devez fournir une unité de mesure pour toutes "
                            "Les questions quantitatives."
                        )
                    )
            if inspection.success:
                inspection.state = "success"
            else:
                inspection.state = "waiting"

    def action_approve(self):
        for inspection in self:
            if inspection.success:
                inspection.state = "success"
            else:
                inspection.state = "failed"

    def action_cancel(self):
        self.write({"state": "canceled"})

    def set_test(self, trigger_line, force_fill=False):
        for inspection in self:
            header = self._prepare_inspection_header(inspection.object_id, trigger_line)
            del header["state"]  # don't change current status
            del header["auto_generated"]  # don't change auto_generated flag
            del header["user"]  # don't change current user
            inspection.write(header)
            inspection.inspection_lines.unlink()
            inspection.inspection_lines = inspection._prepare_inspection_lines(
                trigger_line.test, force_fill=force_fill
            )

    def _make_inspection(self, object_ref, trigger_line):
        """Overridable hook method for creating inspection from test.
        :param object_ref: Object instance
        :param trigger_line: Trigger line instance
        :return: Inspection object
        """
        inspection = self.create(
            self._prepare_inspection_header(object_ref, trigger_line)
        )
        inspection.set_test(trigger_line)
        return inspection

    def _prepare_inspection_header(self, object_ref, trigger_line):
        """Overridable hook method for preparing inspection header.
        :param object_ref: Object instance
        :param trigger_line: Trigger line instance
        :return: List of values for creating the inspection
        """
        return {
            "object_id": object_ref
            and "{},{}".format(object_ref._name, object_ref.id)
            or False,
            "state": "ready",
            "test": trigger_line.test.id,
            "user": trigger_line.user.id,
            "auto_generated": True,
        }

    def _prepare_inspection_lines(self, test, force_fill=False):
        new_data = []
        for line in test.test_lines:
            data = self._prepare_inspection_line(
                test, line, fill=test.fill_correct_values or force_fill
            )
            new_data.append((0, 0, data))
        return new_data

    def _prepare_inspection_line(self, test, line, fill=None):
        data = {
            "name": line.name,
            "test_line": line.id,
            "notes": line.notes,
            "min_value": line.min_value,
            "max_value": line.max_value,
            "test_uom_id": line.uom_id.id,
            "uom_id": line.uom_id.id,
            "question_type": line.type,
            "possible_ql_values": [x.id for x in line.ql_values],
        }
        if fill:
            if line.type == "qualitative":
                # Fill with the first correct value found
                for value in line.ql_values:
                    if value.ok:
                        data["qualitative_value"] = value.id
                        break
            else:
                # Fill with a value inside the interval
                data["quantitative_value"] = (line.min_value + line.max_value) * 0.5
        return data


class QcInspectionLine(models.Model):
    _name = "qc.inspection.line"
    _description = "Quality control inspection line"

    @api.depends(
        "question_type",
        "uom_id",
        "test_uom_id",
        "max_value",
        "min_value",
        "quantitative_value",
        "qualitative_value",
        "possible_ql_values",
    )
    def _compute_quality_test_check(self):
        for insp_line in self:
            if insp_line.question_type == "qualitative":
                insp_line.success = insp_line.qualitative_value.ok
            else:
                if insp_line.uom_id.id == insp_line.test_uom_id.id:
                    amount = insp_line.quantitative_value
                else:
                    amount = self.env["uom.uom"]._compute_quantity(
                        insp_line.quantitative_value, insp_line.test_uom_id.id
                    )
                insp_line.success = insp_line.max_value >= amount >= insp_line.min_value

    @api.depends(
        "possible_ql_values", "min_value", "max_value", "test_uom_id", "question_type"
    )
    def _compute_valid_values(self):
        for insp_line in self:
            if insp_line.question_type == "qualitative":
                insp_line.valid_values = ", ".join(
                    [x.name for x in insp_line.possible_ql_values if x.ok]
                )
            else:
                insp_line.valid_values = "{} ~ {}".format(
                    formatLang(self.env, insp_line.min_value),
                    formatLang(self.env, insp_line.max_value),
                )
                if self.env.ref("uom.group_uom") in self.env.user.groups_id:
                    insp_line.valid_values += " %s" % insp_line.test_uom_id.name

    inspection_id = fields.Many2one(
        comodel_name="qc.inspection", string="Inspection", ondelete="cascade"
    )
    name = fields.Char(string="Question", readonly=True)
    product_id = fields.Many2one(
        comodel_name="product.product",
        related="inspection_id.product_id",
        store=True,
    )
    test_line = fields.Many2one(
        comodel_name="qc.test.question", string="Question de Test ", readonly=True
    )
    possible_ql_values = fields.Many2many(
        comodel_name="qc.test.question.value", string="Réponses"
    )
    quantitative_value = fields.Float(
        string="Valeur quantitative",
        digits="Contrôle de qualité",
        help="Valeur du résultat pour une question quantitative.",
    )
    qualitative_value = fields.Many2one(
        comodel_name="qc.test.question.value",
        string="Valeur qualitative",
        help="Valeur du résultat pour une question qualitative.",
        domain="[('id', 'in', possible_ql_values)]",
    )
    notes = fields.Text()
    min_value = fields.Float(
        string="Min",
        digits="Contrôle de qualité",
        readonly=True,
        help="Valeur minimale valide pour une question quantitative.",
    )
    max_value = fields.Float(
        string="Max",
        digits="Contrôle de qualité",
        readonly=True,
        help="Valeur maximale valide pour une question quantitative.",
    )
    test_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Unité de Test ",
        readonly=True,
        help="Unité pour les valeurs minimales et maximales pour un quantitatif " "question.",
    )
    test_uom_category = fields.Many2one(
        comodel_name="uom.category", related="test_uom_id.category_id", store=True
    )
    uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Unité",
        domain="[('category_id', '=', test_uom_category)]",
        help="UoM of the inspection value for a quantitative question.",
    )
    question_type = fields.Selection(
        [("qualitative", "Qualitatif"), ("quantitative", "Quantitatif")],
        readonly=True,
    )
    valid_values = fields.Char(
        string="Valeurs valides", store=True, compute="_compute_valid_values"
    )
    success = fields.Boolean(
        compute="_compute_quality_test_check", string="Réussi?", store=True
    )