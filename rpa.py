import streamlit as st
import pandas as pd
import requests
import re
import tempfile
import os
import unicodedata
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from io import BytesIO
from zoneinfo import ZoneInfo
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from PIL import Image

# -----------------------------------------------------------------------------
# CONFIGURAÇÃO DA PÁGINA
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Simulador de RPA | Cálculo e Relatórios",
    page_icon="📄",
    layout="wide"
)

# Estilização CSS customizada
st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .metric-title {
        font-size: 0.85rem;
        color: #6c757d;
        font-weight: 600;
        text-transform: uppercase;
    }
    .metric-value {
        font-size: 1.5rem;
        font-weight: bold;
        color: #1e293b;
    }
    .metric-sub {
        font-size: 0.8rem;
        color: #64748b;
        margin-top: 4px;
    }
    .highlight-net { color: #10b981; }
    .highlight-cost { color: #ef4444; }
    .header-value-badge {
        background-color: #1e3a8a;
        color: #ffffff;
        padding: 6px 16px;
        border-radius: 20px;
        font-size: 1.2rem;
        font-weight: bold;
        display: inline-block;
        margin-top: 5px;
    }
    .format-badge {
        color: #3b82f6;
        font-size: 0.82rem;
        font-weight: 600;
        margin-top: -10px;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# FUNÇÕES DE FORMATAÇÃO, DATAS E TRATAMENTO DE TEXTOS / CNPJ / CPF
# -----------------------------------------------------------------------------
def obter_data_atual_br():
    try:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")
    except Exception:
        return datetime.now().strftime("%d/%m/%Y")

def formatar_moeda(valor):
    if valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_cpf(cpf_raw):
    if not cpf_raw or not str(cpf_raw).strip():
        return ""
    numeros = re.sub(r'\D', '', str(cpf_raw))
    if 0 < len(numeros) <= 11:
        numeros = numeros.zfill(11)
        return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"
    return cpf_raw

def formatar_cnpj(cnpj_raw):
    if not cnpj_raw or not str(cnpj_raw).strip():
        return ""
    numeros = re.sub(r'\D', '', str(cnpj_raw))
    if 0 < len(numeros) <= 14:
        numeros = numeros.zfill(14)
        return f"{numeros[:2]}.{numeros[2:5]}.{numeros[5:8]}/{numeros[8:12]}-{numeros[12:]}"
    return cnpj_raw

def sanitizar_nome_arquivo(nome):
    if not nome or not nome.strip():
        return "Autonomo"
    nfkd = unicodedata.normalize('NFKD', nome)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    limpo = re.sub(r'[^\w\s-]', '', sem_acento).strip().replace(' ', '_')
    return limpo if limpo else "Autonomo"

def calcular_diferenca_meses(data_inicio, data_fim):
    if not data_inicio or not data_fim or data_fim < data_inicio:
        return 1
    diff = relativedelta(data_fim, data_inicio)
    meses = diff.years * 12 + diff.months
    if diff.days > 0 or meses == 0:
        meses += 1
    return max(1, meses)

@st.cache_data(ttl=3600)
def consultar_cnpj_api(cnpj_raw):
    cnpj_limpo = re.sub(r'\D', '', str(cnpj_raw))
    if len(cnpj_limpo) != 14:
        return None, "O CNPJ informado deve conter exatamente 14 dígitos."
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        url_brasil = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}"
        resp = requests.get(url_brasil, headers=headers, timeout=(2, 4))
        if resp.status_code == 200:
            dados = resp.json()
            razao = dados.get("razao_social") or dados.get("nome_fantasia")
            if razao:
                return razao, None
        elif resp.status_code == 404:
            return None, "CNPJ não encontrado no banco de dados da Receita Federal."
    except Exception:
        pass

    try:
        url_receitaws = f"https://receitaws.com.br/v1/cnpj/{cnpj_limpo}"
        resp_rws = requests.get(url_receitaws, headers=headers, timeout=(2, 4))
        if resp_rws.status_code == 200:
            dados_rws = resp_rws.json()
            if dados_rws.get("status") == "OK":
                razao = dados_rws.get("nome") or dados_rws.get("fantasia")
                if razao:
                    return razao, None
            elif dados_rws.get("message") == "CNPJ invalido":
                return None, "CNPJ informado é inválido."
    except Exception:
        pass

    return None, "Servidores da Receita temporariamente indisponíveis. Digite a Razão Social manualmente."

# -----------------------------------------------------------------------------
# TABELAS OFICIAIS ATUALIZADAS
# -----------------------------------------------------------------------------
@st.cache_data
def carregar_tabelas_oficiais():
    df_irrf = pd.DataFrame([
        {"Base de Cálculo (R$)": "Até 2.428,80", "Alíquota": "Isento", "Dedução": "R$ 0,00"},
        {"Base de Cálculo (R$)": "2.428,81 a 2.826,65", "Alíquota": "7,5%", "Dedução": "R$ 182,16"},
        {"Base de Cálculo (R$)": "2.826,66 a 3.751,05", "Alíquota": "15,0%", "Dedução": "R$ 394,16"},
        {"Base de Cálculo (R$)": "3.751,06 a 4.664,68", "Alíquota": "22,5%", "Dedução": "R$ 675,49"},
        {"Base de Cálculo (R$)": "Acima de 4.664,68", "Alíquota": "27,5%", "Dedução": "R$ 908,73"}
    ])

    df_redutor = pd.DataFrame([
        {"Rendimento Mensal (R$)": "Até 5.000,00", "Regra de Isenção / Redutor": "Zera o imposto apurado"},
        {"Rendimento Mensal (R$)": "5.000,01 a 7.350,00", "Regra de Isenção / Redutor": "Redução = R$ 978,62 - (0,133145 × Rendimento)"},
        {"Rendimento Mensal (R$)": "Acima de 7.350,00", "Regra de Isenção / Redutor": "Sem redução (Tabela Padrão)"}
    ])

    df_inss = pd.DataFrame([
        {"Categoria": "Prestador PJ (Retenção 11%)", "Teto / Limite": "11% até o Teto (R$ 8.475,55)"},
        {"Categoria": "Prestador PF (Plano Normal)", "Teto / Limite": "20% entre o Mínimo e Teto"},
        {"Categoria": "INSS Patronal (Contratante)", "Teto / Limite": "20% sobre o Valor Bruto Total"}
    ])
    return df_irrf, df_redutor, df_inss

# -----------------------------------------------------------------------------
# MOTORES DE CÁLCULO
# -----------------------------------------------------------------------------
def calcular_inss_prestador(valor_bruto, teto_inss=8475.55, aliquota=0.11, base_inss_recolhida=0.0, inss_ja_descontado=0.0):
    teto_valor_inss = teto_inss * aliquota
    inss_restante_teto = max(0.0, teto_valor_inss - inss_ja_descontado)
    teto_base_restante = max(0.0, teto_inss - base_inss_recolhida)
    
    inss_bruto_calculado = min(valor_bruto, teto_base_restante) * aliquota
    inss_final = min(inss_bruto_calculado, inss_restante_teto)
    return max(0.0, inss_final)

def calcular_irrf_oficial(valor_bruto, desconto_inss, num_dependentes=0, deducao_dependente=189.59, desconto_simplificado=607.20):
    base_deducao_legal = valor_bruto - desconto_inss - (num_dependentes * deducao_dependente)
    base_simplificada = valor_bruto - desconto_simplificado
    
    base_calculo = max(0.0, min(base_deducao_legal, base_simplificada))
    
    if base_calculo <= 2428.80:
        aliquota, deducao = 0.0, 0.0
    elif base_calculo <= 2826.65:
        aliquota, deducao = 0.075, 182.16
    elif base_calculo <= 3751.05:
        aliquota, deducao = 0.15, 394.16
    elif base_calculo <= 4664.68:
        aliquota, deducao = 0.225, 675.49
    else:
        aliquota, deducao = 0.275, 908.73

    imposto_bruto = max(0.0, (base_calculo * aliquota) - deducao)

    if valor_bruto <= 5000.00:
        imposto_final = 0.0
    elif valor_bruto <= 7350.00:
        redutor = max(0.0, 978.62 - (0.133145 * valor_bruto))
        imposto_final = max(0.0, imposto_bruto - redutor)
    else:
        imposto_final = imposto_bruto

    return imposto_final, aliquota, base_calculo

def simular_rpa_completo(v_bruto, teto_inss, num_deps, aliq_iss, reter_iss, calc_patronal, num_meses=1, base_inss_recolhida=0.0, inss_ja_descontado=0.0):
    if v_bruto <= 0:
        return {
            "bruto_mensal": 0.0, "inss_mensal": 0.0, "irrf_mensal": 0.0, "aliq_irrf": 0.0,
            "iss_mensal": 0.0, "tot_descontos_mensal": 0.0, "liquido_mensal": 0.0,
            "patronal_mensal": 0.0, "custo_projeto_mensal": 0.0,
            "num_meses": num_meses,
            "bruto_total": 0.0, "inss_total": 0.0, "irrf_total": 0.0, "iss_total": 0.0,
            "tot_descontos_total": 0.0, "liquido_total": 0.0, "patronal_total": 0.0,
            "custo_projeto_total": 0.0
        }

    v_bruto_m = round(v_bruto, 2)
    desc_inss_m = round(calcular_inss_prestador(v_bruto_m, teto_inss=teto_inss, base_inss_recolhida=base_inss_recolhida, inss_ja_descontado=inss_ja_descontado), 2)
    desc_irrf_m, aliq_irrf, _ = calcular_irrf_oficial(v_bruto_m, desc_inss_m, num_dependentes=num_deps)
    desc_irrf_m = round(desc_irrf_m, 2)
    desc_iss_m = round(v_bruto_m * aliq_iss, 2) if reter_iss else 0.0
    
    tot_descontos_m = desc_inss_m + desc_irrf_m + desc_iss_m
    v_liquido_m = v_bruto_m - tot_descontos_m
    patronal_m = round(v_bruto_m * 0.20, 2) if calc_patronal else 0.0
    custo_projeto_m = v_bruto_m + patronal_m
    
    return {
        "bruto_mensal": v_bruto_m,
        "inss_mensal": desc_inss_m,
        "irrf_mensal": desc_irrf_m,
        "aliq_irrf": aliq_irrf,
        "iss_mensal": desc_iss_m,
        "tot_descontos_mensal": tot_descontos_m,
        "liquido_mensal": v_liquido_m,
        "patronal_mensal": patronal_m,
        "custo_projeto_mensal": custo_projeto_m,
        "num_meses": num_meses,
        "bruto_total": round(v_bruto_m * num_meses, 2),
        "inss_total": round(desc_inss_m * num_meses, 2),
        "irrf_total": round(desc_irrf_m * num_meses, 2),
        "iss_total": round(desc_iss_m * num_meses, 2),
        "tot_descontos_total": round(tot_descontos_m * num_meses, 2),
        "liquido_total": round(v_liquido_m * num_meses, 2),
        "patronal_total": round(patronal_m * num_meses, 2),
        "custo_projeto_total": round(custo_projeto_m * num_meses, 2)
    }

def calcular_bruto_a_partir_do_liquido(v_liquido_alvo, teto_inss, num_deps, aliq_iss, reter_iss, calc_patronal, num_meses=1, base_inss_recolhida=0.0, inss_ja_descontado=0.0):
    if v_liquido_alvo <= 0:
        return 0.0

    low, high = v_liquido_alvo, v_liquido_alvo * 3.0
    for _ in range(50):
        mid = (low + high) / 2
        res = simular_rpa_completo(mid, teto_inss, num_deps, aliq_iss, reter_iss, calc_patronal, num_meses, base_inss_recolhida, inss_ja_descontado)
        if abs(res["liquido_mensal"] - v_liquido_alvo) < 0.001:
            return round(mid, 2)
        elif res["liquido_mensal"] < v_liquido_alvo:
            low = mid
        else:
            high = mid
    return round((low + high) / 2, 2)

def calcular_bruto_a_partir_do_custo_total(custo_total_alvo, teto_inss, num_deps, aliq_iss, reter_iss, calc_patronal, num_meses=1, base_inss_recolhida=0.0, inss_ja_descontado=0.0):
    if custo_total_alvo <= 0:
        return 0.0

    low, high = 0.0, custo_total_alvo
    for _ in range(50):
        mid = (low + high) / 2
        res = simular_rpa_completo(mid, teto_inss, num_deps, aliq_iss, reter_iss, calc_patronal, num_meses, base_inss_recolhida, inss_ja_descontado)
        if abs(res["custo_projeto_mensal"] - custo_total_alvo) < 0.001:
            return round(mid, 2)
        elif res["custo_projeto_mensal"] < custo_total_alvo:
            low = mid
        else:
            high = mid
    return round((low + high) / 2, 2)

# -----------------------------------------------------------------------------
# GERADORES DE RELATÓRIOS (EXCEL E PDF)
# -----------------------------------------------------------------------------
def gerar_excel(dados):
    output = BytesIO()
    label_total_excel = f"Valor Total ({dados['Num Meses']} Meses)" if dados['Num Meses'] > 1 else "Valor Total (1 Mês)"
    data_aprovacao = obter_data_atual_br()
    
    detalhes_fmt = [
        {
            "Rubrica": item["Rubrica"],
            "Tipo": item["Tipo"],
            "Alíquota": item["Referência/Alíquota"],
            "Valor Mensal (R$)": formatar_moeda(item["Valor Mensal"]),
            label_total_excel: formatar_moeda(item["Valor Total"])
        }
        for item in dados["Detalhamento"]
    ]
    
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(detalhes_fmt).to_excel(writer, sheet_name="Recibo RPA", index=False)
        
        resumo_rows = [
            {"Indicador": "Enviado para aprovação em", "Valor Mensal": data_aprovacao, "Valor Total Acumulado": data_aprovacao},
            {"Indicador": "Período do Pagamento", "Valor Mensal": dados["Periodo Texto"], "Valor Total Acumulado": dados["Periodo Texto"]},
            {"Indicador": "Número de Meses", "Valor Mensal": f"{dados['Num Meses']} mês(es)", "Valor Total Acumulado": f"{dados['Num Meses']} mês(es)"},
            {"Indicador": "Dependentes IRRF", "Valor Mensal": str(dados["Num Dependentes"]), "Valor Total Acumulado": str(dados["Num Dependentes"])},
        ]
        
        if dados.get("Lista Vinculos"):
            for idx, vinc in enumerate(dados["Lista Vinculos"], start=1):
                resumo_rows.append({"Indicador": f"Outro Vínculo {idx} (Empresa/CNPJ)", "Valor Mensal": f"{vinc['empresa']} - {vinc['cnpj']}", "Valor Total Acumulado": f"{vinc['empresa']} - {vinc['cnpj']}"})
                resumo_rows.append({"Indicador": f"Outro Vínculo {idx} (Base INSS)", "Valor Mensal": formatar_moeda(vinc['base_inss']), "Valor Total Acumulado": formatar_moeda(vinc['base_inss'])})
                resumo_rows.append({"Indicador": f"Outro Vínculo {idx} (INSS Descontado)", "Valor Mensal": formatar_moeda(vinc['inss_descontado']), "Valor Total Acumulado": formatar_moeda(vinc['inss_descontado'])})
            
        resumo_rows.extend([
            {"Indicador": "Valor Bruto", "Valor Mensal": formatar_moeda(dados["Valor Bruto Mensal"]), "Valor Total Acumulado": formatar_moeda(dados["Valor Bruto Total"])},
            {"Indicador": "Total de Descontos", "Valor Mensal": formatar_moeda(dados["Total Descontos Mensal"]), "Valor Total Acumulado": formatar_moeda(dados["Total Descontos Total"])},
            {"Indicador": "Valor Líquido (Prestador)", "Valor Mensal": formatar_moeda(dados["Valor Líquido Mensal"]), "Valor Total Acumulado": formatar_moeda(dados["Valor Líquido Total"])},
            {"Indicador": "INSS Patronal (Custo Projeto)", "Valor Mensal": formatar_moeda(dados["INSS Patronal Mensal"]), "Valor Total Acumulado": formatar_moeda(dados["INSS Patronal Total"])},
            {"Indicador": "Custo Mensal para o Projeto", "Valor Mensal": formatar_moeda(dados["Custo Total Projeto Mensal"]), "Valor Total Acumulado": formatar_moeda(dados["Custo Total Projeto Total"])}
        ])
        
        pd.DataFrame(resumo_rows).to_excel(writer, sheet_name="Resumo Financeiro", index=False)
        
    output.seek(0)
    return output

def desenhar_faixas_decorativas(canvas, doc, logo_temp_path=None):
    canvas.saveState()
    altura_faixa = 55.0
    y_faixa = letter[1] - altura_faixa
    
    canvas.setFillColor(colors.HexColor('#1E3A8A'))
    canvas.rect(0, y_faixa, letter[0], altura_faixa, fill=1, stroke=0)
    
    x_texto = 36.0
    if logo_temp_path and os.path.exists(logo_temp_path):
        try:
            pil_img = Image.open(logo_temp_path)
            orig_w, orig_h = pil_img.size
            ratio = min(120.0 / orig_w, 42.0 / orig_h)
            new_w, new_h = orig_w * ratio, orig_h * ratio
            
            y_img = y_faixa + ((altura_faixa - new_h) / 2)
            canvas.drawImage(logo_temp_path, 36, y_img, width=new_w, height=new_h, mask='auto')
            x_texto = 36.0 + new_w + 14.0
        except Exception:
            x_texto = 36.0

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(x_texto, y_faixa + 22, "DEMONSTRATIVO DE RECOLHIMENTO A AUTÔNOMO (RPA)")
    
    canvas.setFillColor(colors.HexColor('#1E3A8A'))
    canvas.rect(0, 0, letter[0], 25, fill=1, stroke=0)
    
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(36, 10, "Documento gerado automaticamente pelo Sistema de Cálculo de RPA.")
    canvas.drawRightString(letter[0] - 36, 10, "Página 1 de 1")
    
    canvas.restoreState()

def gerar_pdf(dados, imagem_bytes=None):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=36, leftMargin=36, topMargin=68, bottomMargin=40
    )
    elements = []
    styles = getSampleStyleSheet()
    
    label_style = ParagraphStyle('LabelStyle', parent=styles['Normal'], fontSize=9.5, leading=14, textColor=colors.HexColor('#334155'))
    title_sec = ParagraphStyle('TitleSec', parent=styles['Heading2'], fontSize=11, leading=15, textColor=colors.HexColor('#1E3A8A'), spaceAfter=6)
    aprovacao_style = ParagraphStyle('AprovacaoStyle', parent=styles['Normal'], fontSize=9.5, leading=14, textColor=colors.HexColor('#1E3A8A'))

    logo_temp_path = None
    if imagem_bytes:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(imagem_bytes)
                logo_temp_path = tmp.name
        except Exception:
            logo_temp_path = None

    data_aprovacao = obter_data_atual_br()

    try:
        info_html = f"""
        <b>Tomador:</b> {dados['Contratante']}<br/>
        <b>CNPJ:</b> {dados['CNPJ']}<br/>
        <b>Prestador:</b> {dados['Prestador']}<br/>
        <b>CPF:</b> {dados['CPF']}<br/>
        <b>Serviço:</b> {dados['Servico']}<br/>
        <b>Dependentes (IRRF):</b> {dados['Num Dependentes']}
        """
        
        elements.append(Paragraph(info_html, label_style))
        elements.append(Spacer(1, 6))

        if dados.get("Lista Vinculos"):
            vinculos_html = "<b><u>Outro(s) Vínculo(s):</u></b><br/>"
            for v in dados["Lista Vinculos"]:
                emp_nome = v['empresa'] if v['empresa'] else "Não Informado"
                cnpj_fmt = v['cnpj'] if v['cnpj'] else "Não Informado"
                vinculos_html += f"• <b>Empresa:</b> {emp_nome} (CNPJ: {cnpj_fmt}) | <b>Base INSS:</b> {formatar_moeda(v['base_inss'])} | <b>INSS Descontado:</b> {formatar_moeda(v['inss_descontado'])}<br/>"
            
            elements.append(Paragraph(vinculos_html, label_style))
            elements.append(Spacer(1, 4))
            
        elements.append(Paragraph(f"<b>Período:</b> {dados['Periodo Texto']}", label_style))
        elements.append(Spacer(1, 14))
        elements.append(Paragraph("<b>DETALHAMENTO DOS PROVENTOS E DESCONTOS</b>", title_sec))

        label_total_col = f"Valor Total ({dados['Num Meses']} Meses)" if dados['Num Meses'] > 1 else "Valor Total (1 Mês)"

        table_data = [["Descrição / Rubrica", "Tipo", "Alíquota", "Valor Mensal", label_total_col]]
        for item in dados["Detalhamento"]:
            table_data.append([
                item["Rubrica"],
                item["Tipo"],
                item["Referência/Alíquota"],
                formatar_moeda(item["Valor Mensal"]),
                formatar_moeda(item["Valor Total"])
            ])
            
        t = Table(table_data, colWidths=[160, 65, 65, 125, 125])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,0), 'CENTER'),        
            ('ALIGN', (0,1), (0,-1), 'LEFT'),          
            ('ALIGN', (1,1), (-1,-1), 'CENTER'),       
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 16))
        
        elements.append(Paragraph("<b>DEMONSTRATIVO MENSAL</b>", title_sec))
        resumo_mensal_data = [
            ["VALOR BRUTO MENSAL", formatar_moeda(dados['Valor Bruto Mensal'])],
            ["TOTAL DE DESCONTOS MENSAL", formatar_moeda(dados['Total Descontos Mensal'])],
            ["VALOR LÍQUIDO MENSAL A RECEBER", formatar_moeda(dados['Valor Líquido Mensal'])],
            ["CUSTO MENSAL PARA O PROJETO (com INSS Patronal)", formatar_moeda(dados['Custo Total Projeto Mensal'])]
        ]
        t_res_mensal = Table(resumo_mensal_data, colWidths=[340, 200])
        t_res_mensal.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('LINEABOVE', (0,0), (-1,0), 1.5, colors.HexColor('#1E3A8A')),
            ('TEXTCOLOR', (0,2), (1,2), colors.HexColor('#059669')),
            ('TEXTCOLOR', (0,3), (1,3), colors.HexColor('#DC2626')),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('TOPPADDING', (0,0), (-1,-1), 4),
        ]))
        elements.append(t_res_mensal)
        elements.append(Spacer(1, 14))

        if dados['Num Meses'] > 1:
            elements.append(Paragraph(f"<b>DEMONSTRATIVO ACUMULADO ({dados['Num Meses']} MESES)</b>", title_sec))
            resumo_total_data = [
                ["VALOR BRUTO ACUMULADO", formatar_moeda(dados['Valor Bruto Total'])],
                ["TOTAL DE DESCONTOS ACUMULADOS", formatar_moeda(dados['Total Descontos Total'])],
                ["VALOR LÍQUIDO TOTAL A RECEBER", formatar_moeda(dados['Valor Líquido Total'])],
                ["CUSTO ACUMULADO PARA O PROJETO (com INSS Patronal)", formatar_moeda(dados['Custo Total Projeto Total'])]
            ]
            t_res_total = Table(resumo_total_data, colWidths=[340, 200])
            t_res_total.setStyle(TableStyle([
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
                ('ALIGN', (0,0), (0,-1), 'LEFT'),
                ('ALIGN', (1,0), (1,-1), 'RIGHT'),
                ('LINEABOVE', (0,0), (-1,0), 1.5, colors.HexColor('#1E3A8A')),
                ('TEXTCOLOR', (0,2), (1,2), colors.HexColor('#059669')),
                ('TEXTCOLOR', (0,3), (1,3), colors.HexColor('#DC2626')),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                ('TOPPADDING', (0,0), (-1,-1), 4),
            ]))
            elements.append(t_res_total)
            elements.append(Spacer(1, 14))

        elements.append(Paragraph(f"<b>Enviado para aprovação em:</b> {data_aprovacao}", aprovacao_style))
        
        doc.build(elements, onFirstPage=lambda c, d: desenhar_faixas_decorativas(c, d, logo_temp_path))
    finally:
        if logo_temp_path and os.path.exists(logo_temp_path):
            try:
                os.remove(logo_temp_path)
            except Exception:
                pass

    buffer.seek(0)
    return buffer

# -----------------------------------------------------------------------------
# INTERFACE DO USUÁRIO & GERENCIAMENTO DE ESTADO
# -----------------------------------------------------------------------------
if "contratante_nome" not in st.session_state:
    st.session_state["contratante_nome"] = ""

if "lista_vinculos" not in st.session_state:
    st.session_state["lista_vinculos"] = []

if "temp_vinc_razao" not in st.session_state:
    st.session_state["temp_vinc_razao"] = ""

# Inicialização de variáveis de estado local
cnpj_input = ""
cnpj_formatado = ""
cpf_input = ""
cpf_formatado = ""
vinculos_cadastrados = []
tot_base_inss_outros = 0.0
tot_inss_descontado_outros = 0.0

with st.sidebar:
    st.header("⚙ Configurações e Dados")
    logo_file = st.file_uploader("Envie a Logo da Empresa (PNG, JPG)", type=["png", "jpg", "jpeg"])
    
    st.divider()
    st.subheader("Informações Cadastrais")
    
    col_cnpj_in, col_cnpj_btn = st.columns([3, 1])
    with col_cnpj_in:
        cnpj_input = st.text_input("CNPJ Tomador", value="", key="cnpj_val", placeholder="Apenas números")
    with col_cnpj_btn:
        st.write("")
        st.write("")
        btn_consultar = st.button("🔍", key="btn_cnpj1", help="Buscar Razão Social no governo")

    cnpj_formatado = formatar_cnpj(cnpj_input)
    if cnpj_formatado:
        st.markdown(f'<div class="format-badge">✓ CNPJ Formatado: <b>{cnpj_formatado}</b></div>', unsafe_allow_html=True)

    if btn_consultar and cnpj_input.strip():
        with st.spinner("Consultando Receita Federal..."):
            razao_encontrada, erro_api = consultar_cnpj_api(cnpj_input)
            if razao_encontrada:
                st.session_state["contratante_nome"] = razao_encontrada
                st.success("Razão Social preenchida com sucesso!")
            else:
                st.error(erro_api)

    contratante = st.text_input("Razão Social Contratante", value=st.session_state["contratante_nome"])
    prestador = st.text_input("Nome do Autônomo", value="")
    
    cpf_input = st.text_input("CPF", value="", key="cpf_val", placeholder="Apenas números")
    cpf_formatado = formatar_cpf(cpf_input)
    if cpf_formatado:
        st.markdown(f'<div class="format-badge">✓ CPF Formatado: <b>{cpf_formatado}</b></div>', unsafe_allow_html=True)
    
    descricao_servico = st.text_input("Descrição do Serviço", value="")
    
    st.divider()
    st.subheader("Outro(s) Vínculo(s)")
    tem_outro_vinculo = st.checkbox("Possui outro(s) vínculo(s)?", value=False)

    if tem_outro_vinculo:
        st.caption("Cadastre abaixo um ou mais vínculos onde o autônomo já possui retenção de INSS:")
        
        c_v1, c_v2 = st.columns([3, 1])
        with c_v1:
            cnpj_busca_temp = st.text_input("CNPJ Empresa p/ Busca", key="cnpj_vinc_search", placeholder="Digitar p/ buscar")
        with c_v2:
            st.write("")
            st.write("")
            btn_b_vinc = st.button("🔍", key="btn_b_vinc_action")
            
        if btn_b_vinc and cnpj_busca_temp.strip():
            with st.spinner("Buscando CNPJ..."):
                rz_f, err_f = consultar_cnpj_api(cnpj_busca_temp)
                if rz_f:
                    st.session_state["temp_vinc_razao"] = rz_f
                    st.success("Encontrado!")
                else:
                    st.error(err_f)

        with st.form(key="form_novo_vinculo", clear_on_submit=True):
            f_cnpj = st.text_input("CNPJ da Empresa (Confirmar)", value=cnpj_busca_temp if cnpj_busca_temp else "")
            f_empresa = st.text_input("Razão Social Empresa", value=st.session_state["temp_vinc_razao"])
            f_base = st.number_input("Base de Cálculo INSS (R$)", min_value=0.0, value=0.0, step=100.0)
            f_inss = st.number_input("Valor do INSS Já Descontado (R$)", min_value=0.0, value=0.0, step=10.0)
            
            btn_salvar_vinc = st.form_submit_button("Salvar Vínculo")
            
            if btn_salvar_vinc:
                if f_empresa.strip() or f_cnpj.strip():
                    st.session_state["lista_vinculos"].append({
                        "cnpj": formatar_cnpj(f_cnpj),
                        "empresa": f_empresa,
                        "base_inss": f_base,
                        "inss_descontado": f_inss
                    })
                    st.session_state["temp_vinc_razao"] = ""
                    st.success("Vínculo salvo!")
                    st.rerun()

        if st.session_state["lista_vinculos"]:
            st.markdown("**Vínculos Cadastrados:**")
            idx_para_remover = None
            for idx, item in enumerate(st.session_state["lista_vinculos"]):
                col_item_t, col_item_b = st.columns([4, 1])
                with col_item_t:
                    st.markdown(f"• **{item['empresa']}** ({item['cnpj']})<br/><small>Base: {formatar_moeda(item['base_inss'])} | INSS Desc.: {formatar_moeda(item['inss_descontado'])}</small>", unsafe_allow_html=True)
                with col_item_b:
                    if st.button("🗑", key=f"del_v_{idx}"):
                        idx_para_remover = idx

            if idx_para_remover is not None:
                st.session_state["lista_vinculos"].pop(idx_para_remover)
                st.rerun()

        vinculos_cadastrados = st.session_state["lista_vinculos"]
        tot_base_inss_outros = sum(v["base_inss"] for v in vinculos_cadastrados)
        tot_inss_descontado_outros = sum(v["inss_descontado"] for v in vinculos_cadastrados)

        teto_inss_ref = 8475.55
        if tot_inss_descontado_outros >= (teto_inss_ref * 0.11) or tot_base_inss_outros >= teto_inss_ref:
            st.info("ℹ️ **Isenção de INSS:** O prestador já atingiu o teto máximo de retenção do INSS nos outros vínculos informados. A retenção de INSS neste RPA será de **R$ 0,00**.")

    st.divider()
    st.markdown("**Valor do RPA Mensal**")
    tipo_calculo = st.radio(
        "Modo de entrada:",
        options=[
            "Valor Bruto (Direto)", 
            "Valor Líquido (Cálculo Inverso)", 
            "Custo Total do Projeto (Orçamento Limite)"
        ],
        index=0
    )
    
    if tipo_calculo == "Valor Bruto (Direto)":
        valor_bruto_input = st.number_input("Valor Bruto Mensal do RPA (R$)", min_value=0.0, value=0.0, step=100.0)
    elif tipo_calculo == "Valor Líquido (Cálculo Inverso)":
        valor_liquido_input = st.number_input("Valor Líquido Mensal Desejado (R$)", min_value=0.0, value=0.0, step=100.0)
    else:
        custo_total_input = st.number_input("Custo Total Mensal Limite do Projeto (R$)", min_value=0.0, value=0.0, step=100.0)

    st.divider()
    st.subheader("Período do Pagamento")
    
    data_inicio = st.date_input("Data Inicial do Período (Opcional)", value=None, format="DD/MM/YYYY")
    data_fim = st.date_input("Data Final do Período (Opcional)", value=None, format="DD/MM/YYYY")
    
    if data_inicio and data_fim and data_fim < data_inicio:
        st.warning("⚠ A Data Final deve ser posterior ou igual à Data Inicial.")

    if data_inicio and data_fim:
        meses_calculados = calcular_diferenca_meses(data_inicio, data_fim)
        num_meses = st.number_input("Número de Meses a Pagar", min_value=1, max_value=60, value=meses_calculados, step=1)
        periodo_texto = f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')} ({num_meses} mês/meses)"
    elif data_inicio and not data_fim:
        num_meses = st.number_input("Número de Meses a Pagar", min_value=1, max_value=60, value=1, step=1)
        periodo_texto = f"A partir de {data_inicio.strftime('%d/%m/%Y')} ({num_meses} mês/meses)"
    elif not data_inicio and data_fim:
        num_meses = st.number_input("Número de Meses a Pagar", min_value=1, max_value=60, value=1, step=1)
        periodo_texto = f"Até {data_fim.strftime('%d/%m/%Y')} ({num_meses} mês/meses)"
    else:
        num_meses = st.number_input("Número de Meses a Pagar", min_value=1, max_value=60, value=1, step=1)
        periodo_texto = f"Não Informado ({num_meses} mês/meses)"

    st.divider()
    st.subheader("Valores e Parâmetros")
    
    num_dependentes = st.number_input("Número de Dependentes IRRF", min_value=0, max_value=10, value=0)
    aliquota_iss = st.number_input("Alíquota ISSQN (%)", min_value=0.0, max_value=5.0, value=0.0, step=0.5) / 100
    reter_iss = st.checkbox("Reter ISSQN na Fonte?", value=False)
    patronal_check = st.checkbox("Calcular INSS Patronal (20%)?", value=True)
    teto_inss = st.number_input("Teto do INSS (R$)", min_value=0.0, value=8475.55)

    if tipo_calculo == "Valor Bruto (Direto)":
        valor_bruto_calculado = valor_bruto_input
    elif tipo_calculo == "Valor Líquido (Cálculo Inverso)":
        valor_bruto_calculado = calcular_bruto_a_partir_do_liquido(
            valor_liquido_input, teto_inss, num_dependentes, aliquota_iss, reter_iss, patronal_check, num_meses,
            base_inss_recolhida=tot_base_inss_outros, inss_ja_descontado=tot_inss_descontado_outros
        )
    else:
        valor_bruto_calculado = calcular_bruto_a_partir_do_custo_total(
            custo_total_input, teto_inss, num_dependentes, aliquota_iss, reter_iss, patronal_check, num_meses,
            base_inss_recolhida=tot_base_inss_outros, inss_ja_descontado=tot_inss_descontado_outros
        )

    st.markdown("---")
    st.markdown("<b>🏛 Tabelas Oficiais Vigentes</b>", unsafe_allow_html=True)
    
    df_irrf_oficial, df_redutor_oficial, df_inss_oficial = carregar_tabelas_oficiais()
    
    with st.expander("📌 Ver Tabela IRRF (Base)"):
        st.dataframe(df_irrf_oficial, use_container_width=True, hide_index=True)

    with st.expander("📌 Ver Regra de Redução IRRF"):
        st.dataframe(df_redutor_oficial, use_container_width=True, hide_index=True)

    with st.expander("📌 Ver Tabela INSS (Autônomo/Empresa)"):
        st.dataframe(df_inss_oficial, use_container_width=True, hide_index=True)

# --- PROCESSAMENTO DOS RESULTADOS ---
res = simular_rpa_completo(
    valor_bruto_calculado, teto_inss, num_dependentes, aliquota_iss, reter_iss, patronal_check, num_meses,
    base_inss_recolhida=tot_base_inss_outros, inss_ja_descontado=tot_inss_descontado_outros
)

# --- CABEÇALHO PRINCIPAL ---
col_head1, col_head2 = st.columns([3, 2])

with col_head1:
    if logo_file:
        col_lg, col_tt = st.columns([1, 3])
        with col_lg:
            st.image(logo_file, width=120)
        with col_tt:
            st.title("📄 Emissor e Simulador de RPA")
            st.caption(f"Cálculo de retenções para o período: **{periodo_texto}**")
    else:
        st.title("📄 Emissor e Simulador de RPA")
        st.caption(f"Cálculo de retenções para o período: **{periodo_texto}**")

with col_head2:
    st.markdown(f"""
    <div style="text-align: right; padding-top: 10px;">
        <span style="font-size: 0.9rem; color: #6c757d; font-weight: 600;">TOTAL ACUMULADO ({num_meses} MÊS/MESES)</span><br>
        <span class="header-value-badge">{formatar_moeda(res["bruto_total"])}</span>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# --- PAINEL PRINCIPAL DE RESULTADOS ---
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Valor Bruto Total</div>
        <div class="metric-value">{formatar_moeda(res["bruto_total"])}</div>
        <div class="metric-sub">Mensal: {formatar_moeda(res["bruto_mensal"])}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Total Retenções</div>
        <div class="metric-value">{formatar_moeda(res["tot_descontos_total"])}</div>
        <div class="metric-sub">Mensal: {formatar_moeda(res["tot_descontos_mensal"])}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Líquido Total</div>
        <div class="metric-value highlight-net">{formatar_moeda(res["liquido_total"])}</div>
        <div class="metric-sub">Mensal: {formatar_moeda(res["liquido_mensal"])}</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Custo Acumulado p/ o Projeto</div>
        <div class="metric-value highlight-cost">{formatar_moeda(res["custo_projeto_total"])}</div>
        <div class="metric-sub">Mensal: {formatar_moeda(res["custo_projeto_mensal"])}</div>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# --- DETALHAMENTO DAS RUBRICAS ---
st.subheader(f"📋 Detalhamento do Cálculo ({num_meses} Mês/Meses)")

detalhamento = [
    {
        "Rubrica": "Valor Bruto dos Serviços",
        "Tipo": "Provento",
        "Referência/Alíquota": "-",
        "Valor Mensal": res["bruto_mensal"],
        "Valor Total": res["bruto_total"]
    },
    {
        "Rubrica": "INSS (Prestador)",
        "Tipo": "Desconto",
        "Referência/Alíquota": "11.0%",
        "Valor Mensal": res["inss_mensal"],
        "Valor Total": res["inss_total"]
    },
    {
        "Rubrica": "IRRF",
        "Tipo": "Desconto",
        "Referência/Alíquota": f"{res['aliq_irrf']*100:.1f}%",
        "Valor Mensal": res["irrf_mensal"],
        "Valor Total": res["irrf_total"]
    },
]

if reter_iss:
    detalhamento.append({
        "Rubrica": "ISSQN Retido",
        "Tipo": "Desconto",
        "Referência/Alíquota": f"{aliquota_iss*100:.1f}%",
        "Valor Mensal": res["iss_mensal"],
        "Valor Total": res["iss_total"]
    })

if patronal_check:
    detalhamento.append({
        "Rubrica": "INSS (Patronal)",
        "Tipo": "Encargo",
        "Referência/Alíquota": "20.0%",
        "Valor Mensal": res["patronal_mensal"],
        "Valor Total": res["patronal_total"]
    })

df_resultado = pd.DataFrame(detalhamento)
df_exibicao = df_resultado.copy()
df_exibicao["Valor Mensal"] = df_exibicao["Valor Mensal"].apply(formatar_moeda)
df_exibicao["Valor Total"] = df_exibicao["Valor Total"].apply(formatar_moeda)

df_exibicao_tela = df_exibicao.rename(columns={"Referência/Alíquota": "Alíquota"})

st.dataframe(df_exibicao_tela, use_container_width=True, hide_index=True)

nome_arquivo_prestador = sanitizar_nome_arquivo(prestador)

dados_exportacao = {
    "Contratante": contratante if contratante else "Não Informado",
    "CNPJ": cnpj_formatado if cnpj_formatado else "Não Informado",
    "Prestador": prestador if prestador else "Não Informado",
    "CPF": cpf_formatado if cpf_formatado else "Não Informado",
    "Servico": descricao_servico if descricao_servico else "Não Informado",
    "Num Dependentes": num_dependentes,
    "Lista Vinculos": vinculos_cadastrados,
    "Periodo Texto": periodo_texto,
    "Num Meses": num_meses,
    "Valor Bruto Mensal": res["bruto_mensal"],
    "Valor Bruto Total": res["bruto_total"],
    "Total Descontos Mensal": res["tot_descontos_mensal"],
    "Total Descontos Total": res["tot_descontos_total"],
    "Valor Líquido Mensal": res["liquido_mensal"],
    "Valor Líquido Total": res["liquido_total"],
    "INSS Patronal Mensal": res["patronal_mensal"],
    "INSS Patronal Total": res["patronal_total"],
    "Custo Total Projeto Mensal": res["custo_projeto_mensal"],
    "Custo Total Projeto Total": res["custo_projeto_total"],
    "Detalhamento": detalhamento
}

# --- SEÇÃO DE EXPORTAÇÃO ---
st.divider()
st.subheader("📥 Exportar Relatórios")

col_exp_pdf, col_exp_excel = st.columns(2)
img_bytes = logo_file.getvalue() if logo_file else None

with col_exp_pdf:
    pdf_bytes = gerar_pdf(dados_exportacao, imagem_bytes=img_bytes)
    st.download_button(
        label="📄 Baixar Demonstrativo em PDF",
        data=pdf_bytes,
        file_name=f"Demonstrativo_{nome_arquivo_prestador}_{num_meses}meses.pdf",
        mime="application/pdf",
        use_container_width=True
    )

with col_exp_excel:
    excel_bytes = gerar_excel(dados_exportacao)
    st.download_button(
        label="📊 Baixar Planilha em Excel",
        data=excel_bytes,
        file_name=f"Demonstrativo_{nome_arquivo_prestador}_{num_meses}meses.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )
