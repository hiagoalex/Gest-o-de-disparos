# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional
from datetime import date
import io
import random
import locale
from collections import defaultdict
import os

# reportlab
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm

# local database helper
import database

app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma_chave_secreta_muito_forte_e_dificil'

# locale
try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'Portuguese_Brazil.1252')
    except locale.Error:
        print("Aviso: Configuração de localidade em Português falhou.")

# ------------------- PDF STYLES -------------------
PDF_STYLES = getSampleStyleSheet()
PDF_STYLES.add(ParagraphStyle(name='CustomTitle', fontSize=18, alignment=1, spaceAfter=20, fontName='Helvetica-Bold', textColor=colors.navy))
PDF_STYLES.add(ParagraphStyle(name='CustomHeading2', fontSize=14, alignment=0, spaceBefore=15, spaceAfter=8, fontName='Helvetica-Bold', textColor=colors.darkblue))
PDF_STYLES.add(ParagraphStyle(name='CustomNormalSmall', fontSize=10, alignment=0, spaceAfter=5, textColor=colors.black))
PDF_STYLES.add(ParagraphStyle(name='CustomSummary', fontSize=16, alignment=0, spaceAfter=10, fontName='Helvetica-Bold', textColor=colors.black))

# ------------------- HELPERS -------------------
def gerar_disparos_semanais_simulados():
    dias = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']
    return {dia: random.randint(10, 80) for dia in dias}

def sanitize_filename(s: str):
    if not s:
        return "file"
    allowed = "-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cleaned = "".join(c for c in s if c in allowed)
    return cleaned.replace(" ", "_")

def processar_dados_painel():
    vendedores = database.listar_vendedores()
    for v in vendedores:
        ds = database.get_disparos_semanais(v['id'])
        v['disparos_semanais'] = ds if ds else gerar_disparos_semanais_simulados()
    total_disparos = sum(sum(v['disparos_semanais'].values()) for v in vendedores)
    status_kpis = defaultdict(int)
    vendedores_por_status = defaultdict(list)
    bases_pendentes_count = 0
    dia_bloqueio_count = defaultdict(int)
    for v in vendedores:
        status_kpis[v.get('status','Desconhecido')] += 1
        vendedores_por_status[v.get('status','Desconhecido')].append({
            'nome': v.get('nome'),
            'loja_nome': None,
            'ultimo_status_tipo': v.get('ultimo_status_tipo'),
            'ultimo_status_data': v.get('ultimo_status_data'),
        })
        if v.get('status') == 'Bloqueado':
            dia_semana = date.today().strftime('%A')
            dia_bloqueio_count[dia_semana] += 1
        if not v.get('base_tratada', False):
            bases_pendentes_count += 1
    dia_mais_bloqueio = max(dia_bloqueio_count, key=dia_bloqueio_count.get, default='N/A')
    return {
        'total_disparos': total_disparos,
        'status_kpis': status_kpis,
        'vendedores_por_status': vendedores_por_status,
        'bases_pendentes_count': bases_pendentes_count,
        'dia_mais_bloqueio': dia_mais_bloqueio,
    }

def get_vendedores_by_loja_id(loja_id):
    vendedores = database.get_vendedores_by_loja(loja_id)
    for v in vendedores:
        ds = database.get_disparos_semanais(v['id'])
        v['disparos_semanais'] = ds if ds else gerar_disparos_semanais_simulados()
    return vendedores

# ------------------- FORMS -------------------
class VendedorForm(FlaskForm):
    nome = StringField('Nome', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    loja_id = SelectField('Loja', coerce=int, validators=[DataRequired()])
    status = SelectField('Status Inicial', choices=[('Conectado', 'Conectado'), ('Restrito','Restrito'),('Bloqueado','Bloqueado'),('Desconectado','Desconectado')], validators=[DataRequired()])
    submit = SubmitField('Adicionar Vendedor')

class LojaForm(FlaskForm):
    nome_loja = StringField('Nome da Loja', validators=[DataRequired(), Length(min=3)])
    responsavel = StringField('Responsável', validators=[DataRequired(), Length(min=3)])
    nome_vendedor = StringField('Nome do Gestor (Vendedor)', validators=[DataRequired(), Length(min=2)])
    email_vendedor = StringField('Email do Gestor (Vendedor)', validators=[DataRequired(), Email()])
    submit = SubmitField('Criar Loja')

class LojaEditForm(FlaskForm):
    nome = StringField('Nome da Loja', validators=[DataRequired(), Length(min=3)])
    responsavel = StringField('Responsável', validators=[DataRequired(), Length(min=3)])
    submit = SubmitField('Salvar Alterações')

class RelatorioForm(FlaskForm):
    loja_id_relatorio = SelectField('Selecione a Loja', coerce=int, validators=[DataRequired()])
    ligacoes_realizadas = TextAreaField('SCRIPT DISPAROS DE LIGAÇÕES', validators=[Optional(), Length(max=500)], render_kw={"rows": 5})
    submit = SubmitField('Gerar PDF')
    def __init__(self, *args, **kwargs):
        super(RelatorioForm, self).__init__(*args, **kwargs)
        lojas = database.listar_lojas()
        self.loja_id_relatorio.choices = [(l['id'], l['nome']) for l in lojas]

# ------------------- ROTAS -------------------

@app.route('/')
def index():
    return redirect(url_for('painel'))

@app.route('/painel')
def painel():
    dados_painel = processar_dados_painel()
    vendedor_form = VendedorForm()
    lojas = database.listar_lojas()
    vendedor_form.loja_id.choices = [(l['id'], l['nome']) for l in lojas]
    loja_form = LojaForm()
    loja_edit_form = LojaEditForm()
    relatorio_form = RelatorioForm()
    vendedores = database.listar_vendedores_com_disparos()
    eventos_raw = []
    return render_template('dashboard.html',
                           pagina='painel',
                           today_date=date.today(),
                           db_vendedores=vendedores,
                           eventos=eventos_raw,
                           **dados_painel,
                           vendedor_form=vendedor_form,
                           loja_form=loja_form,
                           loja_edit_form=loja_edit_form,
                           relatorio_form=relatorio_form)

@app.route('/vendedores', methods=['GET', 'POST'])
def vendedores():
    vendedor_form = VendedorForm()
    lojas = database.listar_lojas()
    vendedor_form.loja_id.choices = [(l['id'], l['nome']) for l in lojas]
    relatorio_form = RelatorioForm()

    if vendedor_form.validate_on_submit():
        novo_vendedor = {
            'nome': vendedor_form.nome.data,
            'email': vendedor_form.email.data,
            'loja_id': vendedor_form.loja_id.data,
            'status': vendedor_form.status.data,
            'base_tratada': True,
            'disparos_dia': 0,
            'ultimo_status_tipo': vendedor_form.status.data,
            'ultimo_status_data': date.today().strftime('%d/%m/%Y')
        }
        database.insert_vendedor(novo_vendedor)
        flash(f'Vendedor {novo_vendedor["nome"]} adicionado com sucesso!', 'success')
        return redirect(url_for('vendedores'))

    vendedores = database.listar_vendedores_com_disparos()

    return render_template(
        'dashboard.html',
        pagina='vendedores',
        vendedores=vendedores,
        vendedor_form=vendedor_form,
        loja_form=LojaForm(),
        loja_edit_form=LojaEditForm(),
        relatorio_form=relatorio_form,
        today_date=date.today()
    )

@app.route('/vendedor/<int:vendedor_id>/alternar_base', methods=['POST'])
def alternar_base_tratada(vendedor_id):
    try:
        database.alternar_base_tratada(vendedor_id)
        flash("Base tratada alterada com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao alterar base tratada: {e}", "danger")
    return redirect(url_for('vendedores'))

@app.route('/mudar_status_vendedor/<int:vendedor_id>/<novo_status>', methods=['POST'])
def mudar_status_vendedor(vendedor_id, novo_status):
    try:
        sucesso = database.update_status_vendedor(vendedor_id, novo_status)
        if not sucesso:
            flash("Erro ao alterar status", "danger")
        else:
            flash("Status alterado com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao alterar status: {e}", "danger")
    return redirect(url_for('vendedores'))

# ------------------- ROTAS DE LOJAS -------------------
@app.route('/lojas', methods=['GET','POST'])
def lojas():
    loja_form = LojaForm()
    loja_edit_form = LojaEditForm()
    relatorio_form = RelatorioForm()
    if loja_form.validate_on_submit():
        nova_loja = database.insert_loja(loja_form.nome_loja.data, loja_form.responsavel.data)
        novo_vendedor = {
            'nome': loja_form.nome_vendedor.data,
            'email': loja_form.email_vendedor.data,
            'loja_id': nova_loja['id'],
            'status': 'Conectado',
            'base_tratada': True,
            'disparos_dia': 0,
            'ultimo_status_tipo': 'Conectado',
            'ultimo_status_data': date.today().strftime('%d/%m/%Y')
        }
        database.insert_vendedor(novo_vendedor)
        flash(f"Loja '{nova_loja['nome']}' e Gestor cadastrados com sucesso!", 'success')
        return redirect(url_for('lojas'))
    lojas_com_vendedores = []
    for loja in database.listar_lojas():
        loja_copy = loja.copy()
        loja_copy['vendedores'] = database.get_vendedores_by_loja(loja['id'])
        lojas_com_vendedores.append(loja_copy)
    return render_template('dashboard.html',
                           pagina='lojas',
                           lojas=lojas_com_vendedores,
                           vendedor_form=VendedorForm(),
                           loja_form=loja_form,
                           loja_edit_form=loja_edit_form,
                           relatorio_form=relatorio_form,
                           today_date=date.today())

@app.route('/editar_loja', methods=['POST'])
def editar_loja():
    form = LojaEditForm(request.form)
    loja_id = int(request.form.get('loja_id'))
    if form.validate_on_submit():
        database.update_loja(loja_id, form.nome.data, form.responsavel.data)
        flash('Loja atualizada com sucesso!', 'success')
    else:
        flash('Erro de validação ao editar a loja.', 'warning')
    return redirect(url_for('lojas'))

# ------------------- ROTAS DE PDF -------------------
@app.route('/gerar_relatorio_pdf', methods=['POST'])
def gerar_relatorio_pdf():
    form = RelatorioForm(request.form)
    lojas = database.listar_lojas()
    form.loja_id_relatorio.choices = [(l['id'], l['nome']) for l in lojas]
    if form.validate_on_submit():
        loja_id = form.loja_id_relatorio.data
        ligacoes_realizadas = form.ligacoes_realizadas.data
        loja_data = database.get_loja_by_id(loja_id)
        vendedores_loja = get_vendedores_by_loja_id(loja_id)
        if not loja_data:
            flash(f"Erro: Loja com ID {loja_id} não encontrada.", 'danger')
            return redirect(url_for('painel'))
        try:
            from pdf_generator import gerar_pdf_reportlab
            pdf_buffer, disk_path = gerar_pdf_reportlab(loja_data, vendedores_loja, ligacoes_realizadas)
            filename = os.path.basename(disk_path)
            return send_file(pdf_buffer, as_attachment=True, download_name=filename)
        except Exception as e:
            flash(f"Erro ao gerar PDF: {str(e)}", 'danger')
            return redirect(url_for('painel'))
    flash("Formulário inválido!", "warning")
    return redirect(url_for('painel'))

if __name__ == '__main__':
    app.run(debug=True)
