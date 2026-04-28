"""One-shot generator for French-language fictitious PE sample documents.

Produces:
  sample-docs/Fictitious_PE_Capital_Call_FR_1.pdf
  sample-docs/Fictitious_PE_Distribution_Notice_FR_1.pdf
  sample-docs/Fictitious_PE_Capital_Call_FR_1.csv

All data is fictitious and intended for testing the document classification
pipeline against French-language Luxembourg PE fund documents.
"""
from __future__ import annotations

import csv
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample-docs"
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontSize=18,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#1f3864"),
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontSize=10,
            leading=14,
        ),
        "small": ParagraphStyle(
            "small",
            parent=base["BodyText"],
            fontSize=8,
            leading=10,
            textColor=colors.grey,
        ),
    }


def _kv_table(rows: list[tuple[str, str]]) -> Table:
    table = Table(rows, colWidths=[6 * cm, 10 * cm])
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ]
        )
    )
    return table


def build_capital_call_pdf() -> Path:
    out = SAMPLE_DIR / "Fictitious_PE_Capital_Call_FR_1.pdf"
    s = _styles()
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="Avis d'Appel de Fonds",
        author="Luxembourg Private Equity Partners S.C.A.",
    )

    flow: list = []
    flow.append(Paragraph("AVIS D'APPEL DE FONDS", s["title"]))
    flow.append(Paragraph("Luxembourg Private Equity Partners S.C.A., SICAV-RAIF", s["body"]))
    flow.append(Paragraph("Compartiment : LPEP Growth Fund III", s["body"]))
    flow.append(Spacer(1, 12))

    flow.append(Paragraph("Référence du document", s["h2"]))
    flow.append(
        _kv_table(
            [
                ("Numéro d'avis", "CC-2026-007"),
                ("Date d'émission", "28 avril 2026"),
                ("Date d'échéance", "15 mai 2026"),
                ("Devise", "EUR"),
                ("Investisseur", "Caisse de Pension Modèle S.A."),
                ("Numéro d'engagement", "INV-LU-00421"),
            ]
        )
    )

    flow.append(Paragraph("Détails de l'appel", s["h2"]))
    flow.append(
        _kv_table(
            [
                ("Engagement total", "EUR 5 000 000,00"),
                ("Capital appelé à ce jour", "EUR 1 500 000,00"),
                ("Pourcentage appelé cumulé", "30,00 %"),
                ("Montant du présent appel", "EUR 750 000,00"),
                ("Pourcentage du présent appel", "15,00 %"),
                ("Engagement restant après appel", "EUR 2 750 000,00"),
            ]
        )
    )

    flow.append(Paragraph("Affectation du présent appel", s["h2"]))
    affectation = [
        ["Objet", "Montant (EUR)", "Part"],
        ["Investissements (Newco Holdings S.à r.l.)", "600 000,00", "80 %"],
        ["Frais de gestion", "112 500,00", "15 %"],
        ["Frais d'administration et dépositaire", "37 500,00", "5 %"],
        ["Total", "750 000,00", "100 %"],
    ]
    t = Table(affectation, colWidths=[8 * cm, 4 * cm, 4 * cm])
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3864")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 10),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    flow.append(t)

    flow.append(Paragraph("Instructions de virement", s["h2"]))
    flow.append(
        _kv_table(
            [
                ("Bénéficiaire", "LPEP Growth Fund III - Compte d'Appels"),
                ("Banque", "Banque de Luxembourg S.A."),
                ("IBAN", "LU28 0019 4006 4475 0000"),
                ("BIC / SWIFT", "BLUXLULL"),
                ("Référence à mentionner", "CC-2026-007 / INV-LU-00421"),
            ]
        )
    )

    flow.append(Spacer(1, 12))
    flow.append(
        Paragraph(
            "Le paiement doit parvenir au compte du Compartiment au plus tard à la date "
            "d'échéance indiquée ci-dessus. À défaut, des intérêts de retard pourront être "
            "appliqués conformément aux dispositions du Document d'émission. Pour toute "
            "question, veuillez contacter notre service Investisseurs : "
            "investisseurs@lpep.lu, +352 27 12 34 56.",
            s["body"],
        )
    )

    flow.append(Spacer(1, 18))
    flow.append(
        Paragraph(
            "Document fictif généré à des fins de test. "
            "Aucune donnée personnelle ou financière réelle n'est représentée.",
            s["small"],
        )
    )

    doc.build(flow)
    return out


def build_distribution_notice_pdf() -> Path:
    out = SAMPLE_DIR / "Fictitious_PE_Distribution_Notice_FR_1.pdf"
    s = _styles()
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="Avis de Distribution",
        author="Luxembourg Private Equity Partners S.C.A.",
    )

    flow: list = []
    flow.append(Paragraph("AVIS DE DISTRIBUTION", s["title"]))
    flow.append(Paragraph("Luxembourg Private Equity Partners S.C.A., SICAV-RAIF", s["body"]))
    flow.append(Paragraph("Compartiment : LPEP Growth Fund III", s["body"]))
    flow.append(Spacer(1, 12))

    flow.append(Paragraph("Référence du document", s["h2"]))
    flow.append(
        _kv_table(
            [
                ("Numéro d'avis", "DIS-2026-004"),
                ("Date d'émission", "28 avril 2026"),
                ("Date de paiement", "12 mai 2026"),
                ("Devise", "EUR"),
                ("Investisseur", "Caisse de Pension Modèle S.A."),
                ("Numéro d'engagement", "INV-LU-00421"),
            ]
        )
    )

    flow.append(Paragraph("Détails de la distribution", s["h2"]))
    flow.append(
        _kv_table(
            [
                ("Engagement total", "EUR 5 000 000,00"),
                ("Capital appelé cumulé", "EUR 2 250 000,00"),
                ("Distributions cumulées avant ce versement", "EUR 600 000,00"),
                ("Montant de la présente distribution", "EUR 425 000,00"),
                ("Distributions cumulées après ce versement", "EUR 1 025 000,00"),
                ("Origine de la distribution", "Cession partielle - Newco Holdings S.à r.l."),
            ]
        )
    )

    flow.append(Paragraph("Ventilation par nature", s["h2"]))
    ventilation = [
        ["Nature", "Montant (EUR)", "Part"],
        ["Remboursement de capital", "175 000,00", "41,18 %"],
        ["Plus-value réalisée", "225 000,00", "52,94 %"],
        ["Revenus d'intérêts", "25 000,00", "5,88 %"],
        ["Total brut", "425 000,00", "100,00 %"],
        ["Retenue à la source (le cas échéant)", "0,00", "0,00 %"],
        ["Net à verser", "425 000,00", "100,00 %"],
    ]
    t = Table(ventilation, colWidths=[8 * cm, 4 * cm, 4 * cm])
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3864")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 10),
                ("FONT", (0, 3), (-1, 3), "Helvetica-Bold", 10),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, 3), (-1, 3), 0.5, colors.grey),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    flow.append(t)

    flow.append(Paragraph("Instructions de paiement", s["h2"]))
    flow.append(
        _kv_table(
            [
                ("Compte de virement", "Compte indiqué dans la Convention de souscription"),
                ("Banque émettrice", "Banque de Luxembourg S.A."),
                ("IBAN du Compartiment", "LU28 0019 4006 4475 0000"),
                ("BIC / SWIFT", "BLUXLULL"),
                ("Référence", "DIS-2026-004 / INV-LU-00421"),
            ]
        )
    )

    flow.append(Spacer(1, 12))
    flow.append(
        Paragraph(
            "Le présent avis ne constitue pas un avis fiscal. Les investisseurs sont "
            "invités à consulter leur conseiller pour le traitement fiscal applicable "
            "dans leur juridiction. Pour toute question opérationnelle, veuillez "
            "contacter le service Investisseurs : investisseurs@lpep.lu, +352 27 12 34 56.",
            s["body"],
        )
    )

    flow.append(Spacer(1, 18))
    flow.append(
        Paragraph(
            "Document fictif généré à des fins de test. "
            "Aucune donnée personnelle ou financière réelle n'est représentée.",
            s["small"],
        )
    )

    doc.build(flow)
    return out


def build_capital_call_csv() -> Path:
    out = SAMPLE_DIR / "Fictitious_PE_Capital_Call_FR_1.csv"
    rows = [
        # Header
        [
            "notice_id",
            "issue_date",
            "due_date",
            "fund_name",
            "compartment",
            "investor_id",
            "investor_name",
            "currency",
            "total_commitment",
            "called_to_date",
            "current_call_amount",
            "current_call_pct",
            "remaining_commitment",
            "purpose",
            "iban",
            "bic",
            "payment_reference",
        ],
        [
            "CC-2026-007",
            "2026-04-28",
            "2026-05-15",
            "LPEP Growth Fund III",
            "LPEP-GF-III",
            "INV-LU-00421",
            "Caisse de Pension Modèle S.A.",
            "EUR",
            "5000000.00",
            "1500000.00",
            "750000.00",
            "0.15",
            "2750000.00",
            "Investissement Newco Holdings + frais",
            "LU28 0019 4006 4475 0000",
            "BLUXLULL",
            "CC-2026-007/INV-LU-00421",
        ],
        [
            "CC-2026-007",
            "2026-04-28",
            "2026-05-15",
            "LPEP Growth Fund III",
            "LPEP-GF-III",
            "INV-LU-00422",
            "Fondation Patrimoine Lux",
            "EUR",
            "3000000.00",
            "900000.00",
            "450000.00",
            "0.15",
            "1650000.00",
            "Investissement Newco Holdings + frais",
            "LU28 0019 4006 4475 0000",
            "BLUXLULL",
            "CC-2026-007/INV-LU-00422",
        ],
        [
            "CC-2026-007",
            "2026-04-28",
            "2026-05-15",
            "LPEP Growth Fund III",
            "LPEP-GF-III",
            "INV-LU-00423",
            "Banque Privée Échantillon S.A.",
            "EUR",
            "10000000.00",
            "3000000.00",
            "1500000.00",
            "0.15",
            "5500000.00",
            "Investissement Newco Holdings + frais",
            "LU28 0019 4006 4475 0000",
            "BLUXLULL",
            "CC-2026-007/INV-LU-00423",
        ],
        [
            "CC-2026-007",
            "2026-04-28",
            "2026-05-15",
            "LPEP Growth Fund III",
            "LPEP-GF-III",
            "INV-LU-00424",
            "Family Office Luxembourgeois Sàrl",
            "EUR",
            "2000000.00",
            "600000.00",
            "300000.00",
            "0.15",
            "1100000.00",
            "Investissement Newco Holdings + frais",
            "LU28 0019 4006 4475 0000",
            "BLUXLULL",
            "CC-2026-007/INV-LU-00424",
        ],
        [
            "CC-2026-007",
            "2026-04-28",
            "2026-05-15",
            "LPEP Growth Fund III",
            "LPEP-GF-III",
            "INV-LU-00425",
            "Assurance Vie Modèle S.A.",
            "EUR",
            "7500000.00",
            "2250000.00",
            "1125000.00",
            "0.15",
            "4125000.00",
            "Investissement Newco Holdings + frais",
            "LU28 0019 4006 4475 0000",
            "BLUXLULL",
            "CC-2026-007/INV-LU-00425",
        ],
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return out


if __name__ == "__main__":
    paths = [
        build_capital_call_pdf(),
        build_distribution_notice_pdf(),
        build_capital_call_csv(),
    ]
    for p in paths:
        print(f"Wrote: {p} ({p.stat().st_size} bytes)")
