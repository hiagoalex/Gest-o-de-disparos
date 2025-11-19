import os
import sys
import json
from datetime import date, datetime, timedelta
import psycopg2
from psycopg2 import extras
from psycopg2.errors import UniqueViolation, ForeignKeyViolation
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, SubmitField, HiddenField, EmailField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional
import random
import locale
import io

# IMPORTAÇÃO DA BIBLIOTECA DE PDF
from xhtml2pdf import pisa

# --- Configuração da Aplicação ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave_secreta_padrao_segura') 

# --- CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE) ---
# O código tentará ler do Render (Variáveis de Ambiente).
# Se não encontrar (rodando local), usa os valores padrão que pegamos da sua imagem.

DB_HOST = os.environ.get('DB_HOST', 'db.sorzeppofdsegjsocujk.supabase.co')
DB_NAME = os.environ.get('DB_NAME', 'postgres')
DB_USER = os.environ.get('DB_USER', 'postgres')
DB_PASS = os.environ.get('DB_PASS') # A SENHA DEVE ESTAR NAS VARIÁVEIS DE AMBIENTE!
DB_PORT = os.environ.get('DB_PORT', '5432')

# Verifica se a senha foi configurada
if not DB_PASS:
    print("AVISO CRÍTICO: A variável DB_PASS (senha do banco) não foi encontrada!", file=sys.stderr)

# Configuração de localidade
try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'Portuguese_Brazil.1252')
    except locale.Error:
        pass

# --- Gerenciador de Banco de Dados ---
class DatabaseManager:
    def __init__(self):
        self.conn = None

    def get_connection(self):
        """Cria e retorna uma nova conexão com o banco."""
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                port=DB_PORT
            )
            return conn
        except psycopg2.Error as e:
            print(f"Erro ao conectar ao PostgreSQL: {e}", file=sys.stderr)
            return None

    def execute_query(self, query, params=None, fetch_one=False, commit=False):
        """Executa query de forma segura, abrindo e fechando conexão."""
        conn = self.get_connection()
        if not conn:
            return None

        try:
            with conn.cursor(cursor_factory=extras.DictCursor) as cur:
                cur.execute(query, params)
                
                if commit:
                    conn.commit()
                    return True
                
                if fetch_one:
                    return cur.fetchone()

                return cur.fetchall()

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            print(f"ERRO SQL: {e}", file=sys.stderr)
            raise e # Repassa o erro para ser tratado na rota
        finally:
            if conn:
                conn.close()

db_manager = DatabaseManager()

# --- Funções Auxiliares ---

def gerar_disparos_semanais_simulados():
    """Gera JSON para disparos iniciais."""
    return extras.Json({
        'segunda': 0, 'terca': 0, 'quarta': 0, 'quinta': 0, 'sexta': 0, 'sabado': 0, 'domingo': 0
    })

def get_lojas_choices():
    """Busca lojas para o SelectField."""
    query = "SELECT id, nome FROM lojas ORDER BY nome;"
    try:
        lojas = db_manager.execute_query(query)
        return [(l['id'], l['nome']) for l in lojas] if lojas else []
    except:
        return []

def get_loja_by_id(loja_id):
    query = "SELECT * FROM lojas WHERE id = %s"
    return db_manager.execute_query(query, (loja_id,), fetch_one=True)

def get_vendedor_by_id(vendedor_id):
    query = "SELECT * FROM vendedores WHERE id = %s"
    return db_manager.execute_query(query, (vendedor_id,), fetch_one=True)

# --- Formulários ---

class LojaForm(FlaskForm):
    nome_loja = StringField('Nome da Loja', validators=[DataRequired(), Length(min=3)])
    responsavel = StringField('Responsável', validators=[DataRequired(), Length(min=3)])
    nome_vendedor = StringField('Nome do Gestor', validators=[DataRequired()])
    email_vendedor = StringField('Email do Gestor', validators=[DataRequired(), Email()])
    submit = SubmitField('Criar Loja')

class LojaEditForm(FlaskForm):
    nome = StringField('Nome da Loja', validators=[DataRequired()])
    responsavel = StringField('Responsável', validators=[DataRequired()])
    submit = SubmitField('Salvar Alterações')

class VendedorForm(FlaskForm):
    nome = StringField('Nome', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    loja_id = SelectField('Loja', coerce=int, validators=[DataRequired()])
    status = SelectField('Status', choices=[
        ('Conectado', 'Conectado'), ('Restrito', 'Restrito'), 
        ('Bloqueado', 'Bloqueado'), ('Desconectado', 'Desconectado')
    ])
    submit = SubmitField('Adicionar Vendedor')

class RelatorioPDFForm(FlaskForm):
    loja_id_relatorio = SelectField('Selecione a Loja', coerce=int, validators=[DataRequired()])
    ligacoes_realizadas = TextAreaField('Script de Ligações / Follow-up', validators=[Optional()])
    submit = SubmitField('Gerar PDF')

# --- ROTAS ---

@app.route('/')
def index():
    return redirect(url_for('painel'))

@app.route('/painel')
def painel():
    # 1. Carregar dados do Banco
    try:
        vendedores_db = db_manager.execute_query("SELECT * FROM vendedores ORDER BY nome") or []
        lojas_db = db_manager.execute_query("SELECT * FROM lojas ORDER BY nome") or []
        eventos_db = [] # Implementar tabela de eventos se necessário
    except Exception as e:
        print(f"Erro ao carregar painel: {e}")
        vendedores_db = []
        lojas_db = []

    # 2. Calcular KPIs
    total_disparos = 0
    status_kpis = defaultdict(int)
    bases_pendentes_count = 0
    bloqueados_hoje = []
    hoje_str = date.today().strftime('%d/%m/%Y') # Formato string para comparar se viesse string

    vendedores_formatados = []

    from collections import defaultdict
    vendedores_por_status = defaultdict(list)
    
    for v in vendedores_db:
        # Somar disparos da semana (JSON)
        disparos_semanais = v['disparos_semanais'] if v['disparos_semanais'] else {}
        total_disparos += sum(disparos_semanais.values())
        
        status_kpis[v['status']] += 1
        
        # Formatar datas
        data_ultimo = v['ultimo_status_data'].strftime('%d/%m/%Y') if v['ultimo_status_data'] else 'N/A'
        
        v_obj = {
            'id': v['id'],
            'nome': v['nome'],
            'email': v['email'],
            'loja': v['loja_nome'],
            'loja_id': v['loja_id'],
            'status': v['status'],
            'base_tratada': v['base_tratada'],
            'disparos_dia': v['disparos_dia'],
            'disparos_semanais': disparos_semanais,
            'ultimo_status_tipo': v['ultimo_status_tipo'],
            'ultimo_status_data': data_ultimo
        }
        
        vendedores_formatados.append(v_obj)
        vendedores_por_status[v['status']].append(v_obj)
        
        if not v['base_tratada']:
            bases_pendentes_count += 1
            
        # Verifica bloqueados hoje (comparando data objeto)
        if v['status'] == 'Bloqueado' and v['ultimo_status_data'] == date.today():
            bloqueados_hoje.append(v_obj)

    # Prepara forms vazios para os modais
    vendedor_form = VendedorForm()
    if lojas_db:
        vendedor_form.loja_id.choices = [(l['id'], l['nome']) for l in lojas_db]
    else:
        vendedor_form.loja_id.choices = [(0, 'Nenhuma loja')]

    relatorio_form = RelatorioPDFForm()
    if lojas_db:
        relatorio_form.loja_id_relatorio.choices = [(0, 'Selecione a Loja')] + [(l['id'], l['nome']) for l in lojas_db]
    else:
        relatorio_form.loja_id_relatorio.choices = [(0, 'Nenhuma loja')]
    
    loja_form = None 
    loja_edit_form = None

    return render_template('dashboard.html',
        pagina='painel',
        today_date=date.today(),
        total_disparos=total_disparos,
        status_kpis=status_kpis,
        bases_pendentes_count=bases_pendentes_count,
        bloqueados_hoje=bloqueados_hoje,
        vendedores_por_status=vendedores_por_status,
        eventos=[], # Lista vazia por enquanto
        dia_mais_bloqueio="N/A",
        relatorio_form=relatorio_form,
        vendedor_form=vendedor_form,
        loja_form=loja_form,
        loja_edit_form=loja_edit_form
    )

@app.route('/lojas', methods=['GET', 'POST'])
def lojas():
    loja_form = LojaForm()
    loja_edit_form = LojaEditForm()
    
    if loja_form.validate_on_submit():
        try:
            # 1. Inserir Loja
            query_loja = "INSERT INTO lojas (nome, responsavel) VALUES (%s, %s) RETURNING id"
            loja_id = db_manager.execute_query(query_loja, (loja_form.nome_loja.data, loja_form.responsavel.data), fetch_one=True, commit=True)
            
            if loja_id:
                # 2. Inserir Gestor
                query_vendedor = """
                    INSERT INTO vendedores (nome, email, loja_id, loja_nome, status, base_tratada, disparos_dia, disparos_semanais, ultimo_status_tipo, ultimo_status_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                params_vendedor = (
                    loja_form.nome_vendedor.data,
                    loja_form.email_vendedor.data,
                    loja_id[0],
                    loja_form.nome_loja.data,
                    'Conectado',
                    True,
                    0,
                    gerar_disparos_semanais_simulados(),
                    'Conectado',
                    date.today()
                )
                db_manager.execute_query(query_vendedor, params_vendedor, commit=True)
                flash('Loja e Gestor criados com sucesso!', 'success')
            return redirect(url_for('lojas'))

        except UniqueViolation:
            flash('Erro: Já existe uma loja com este nome.', 'danger')
            return redirect(url_for('lojas'))
        except Exception as e:
            flash(f'Erro ao criar loja: {e}', 'danger')
            return redirect(url_for('lojas'))

    # Listagem
    lojas_db = db_manager.execute_query("SELECT * FROM lojas ORDER BY nome") or []
    vendedores_db = db_manager.execute_query("SELECT * FROM vendedores") or []
    
    # Anexa vendedores às lojas para exibição
    lojas_com_vendedores = []
    for loja in lojas_db:
        l_dict = dict(loja)
        l_dict['vendedores'] = [v for v in vendedores_db if v['loja_id'] == l_dict['id']]
        lojas_com_vendedores.append(l_dict)

    return render_template('dashboard.html',
        pagina='lojas',
        lojas=lojas_com_vendedores,
        loja_form=loja_form,
        loja_edit_form=loja_edit_form,
        vendedor_form=None,
        today_date=date.today()
    )

@app.route('/vendedores', methods=['GET', 'POST'])
def vendedores():
    vendedor_form = VendedorForm()
    lojas = get_lojas_choices()
    vendedor_form.loja_id.choices = lojas

    if vendedor_form.validate_on_submit():
        try:
            loja_info = get_loja_by_id(vendedor_form.loja_id.data)
            
            query = """
                INSERT INTO vendedores (nome, email, loja_id, loja_nome, status, base_tratada, disparos_dia, disparos_semanais, ultimo_status_tipo, ultimo_status_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            params = (
                vendedor_form.nome.data,
                vendedor_form.email.data,
                vendedor_form.loja_id.data,
                loja_info['nome'],
                vendedor_form.status.data,
                False, # Base pendente
                0,
                gerar_disparos_semanais_simulados(),
                vendedor_form.status.data,
                date.today()
            )
            db_manager.execute_query(query, params, commit=True)
            flash('Vendedor adicionado!', 'success')
            return redirect(url_for('vendedores'))
            
        except UniqueViolation:
            flash('Erro: Email já cadastrado.', 'danger')
            return redirect(url_for('vendedores'))
        except Exception as e:
            flash(f'Erro ao adicionar: {e}', 'danger')
            return redirect(url_for('vendedores'))

    # Listagem com filtros
    query = "SELECT * FROM vendedores"
    params = []
    
    loja_id_filtro = request.args.get('loja_id')
    status_filtro = request.args.get('status')
    
    if loja_id_filtro or status_filtro:
        query += " WHERE"
        conditions = []
        if loja_id_filtro:
            conditions.append(" loja_id = %s")
            params.append(loja_id_filtro)
        if status_filtro:
            conditions.append(" status = %s")
            params.append(status_filtro)
        query += " AND".join(conditions)
    
    query += " ORDER BY nome"
    
    vendedores_raw = db_manager.execute_query(query, tuple(params)) or []
    
    # Formata para o template
    vendedores_fmt = []
    for v in vendedores_raw:
        v_dict = dict(v)
        v_dict['ultimo_status_data'] = v['ultimo_status_data'].strftime('%d/%m/%Y') if v['ultimo_status_data'] else 'N/A'
        vendedores_fmt.append(v_dict)

    return render_template('dashboard.html',
        pagina='vendedores',
        vendedores=vendedores_fmt,
        lojas_raw=db_manager.execute_query("SELECT * FROM lojas ORDER BY nome") or [],
        vendedor_form=vendedor_form,
        today_date=date.today(),
        loja_id_filtro_ativo=int(loja_id_filtro) if loja_id_filtro else None
    )

@app.route('/editar_loja', methods=['POST'])
def editar_loja():
    form = LojaEditForm()
    try:
        loja_id = request.form.get('loja_id')
        query = "UPDATE lojas SET nome = %s, responsavel = %s WHERE id = %s"
        db_manager.execute_query(query, (form.nome.data, form.responsavel.data, loja_id), commit=True)
        
        # Atualiza nome da loja nos vendedores
        db_manager.execute_query("UPDATE vendedores SET loja_nome = %s WHERE loja_id = %s", (form.nome.data, loja_id), commit=True)
        
        flash('Loja atualizada.', 'success')
    except Exception as e:
        flash(f'Erro ao editar loja: {e}', 'danger')
    return redirect(url_for('lojas'))

@app.route('/mudar_status_vendedor/<int:vendedor_id>/<novo_status>', methods=['POST'])
def mudar_status_vendedor(vendedor_id, novo_status):
    try:
        query = "UPDATE vendedores SET status = %s, ultimo_status_tipo = %s, ultimo_status_data = %s WHERE id = %s"
        db_manager.execute_query(query, (novo_status, novo_status, date.today(), vendedor_id), commit=True)
        flash(f'Status alterado para {novo_status}.', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('vendedores', **request.args))

@app.route('/alternar_base_tratada/<int:vendedor_id>', methods=['POST'])
def alternar_base_tratada(vendedor_id):
    try:
        # Pega estado atual
        curr = get_vendedor_by_id(vendedor_id)
        novo_estado = not curr['base_tratada']
        db_manager.execute_query("UPDATE vendedores SET base_tratada = %s WHERE id = %s", (novo_estado, vendedor_id), commit=True)
        flash('Status da base atualizado.', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('vendedores', **request.args))

@app.route('/editar_disparos_dia', methods=['POST'])
def editar_disparos_dia():
    try:
        vid = request.form.get('vendedor_id')
        qtd = request.form.get('disparos_hoje')
        db_manager.execute_query("UPDATE vendedores SET disparos_dia = %s WHERE id = %s", (qtd, vid), commit=True)
        flash('Disparos do dia atualizados.', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('vendedores', **request.args))

@app.route('/editar_disparos_semana', methods=['POST'])
def editar_disparos_semana():
    try:
        vid = request.form.get('vendedor_id')
        dias = ['segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo']
        novos_dados = {}
        for dia in dias:
            novos_dados[dia] = int(request.form.get(f'disparo_{dia}', 0))
        
        # Salva como JSONB
        db_manager.execute_query("UPDATE vendedores SET disparos_semanais = %s WHERE id = %s", (extras.Json(novos_dados), vid), commit=True)
        flash('Disparos semanais atualizados.', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('vendedores', **request.args))

@app.route('/relatorio/gerar', methods=['POST'])
def gerar_relatorio_pdf():
    form = RelatorioForm()
    form.loja_id_relatorio.choices = [(0, 'Sel')] + get_loja_choices() # Hack para validar
    
    try:
        loja_id = request.form.get('loja_id_relatorio')
        if not loja_id or loja_id == '0':
            flash('Selecione uma loja.', 'warning')
            return redirect(url_for('painel'))
            
        loja = get_loja_by_id(loja_id)
        vendedores = get_vendedores_by_loja_id(loja_id)
        texto = request.form.get('ligacoes_realizadas')
        
        # Renderiza HTML
        html = render_template('relatorio_template.html', 
            loja=loja, 
            vendedores_loja=vendedores, 
            ligacoes_realizadas=texto,
            data_hoje=date.today().strftime('%d/%m/%Y'),
            total_convites_enviados=sum(sum(v['disparos_semanais'].values()) for v in vendedores)
        )
        
        # Gera PDF
        pdf_file = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf_file)
        pdf_file.seek(0)
        
        return send_file(pdf_file, mimetype='application/pdf', as_attachment=True, download_name=f'Relatorio_{loja["nome"]}.pdf')
        
    except Exception as e:
        flash(f'Erro ao gerar PDF: {e}', 'danger')
        return redirect(url_for('painel'))

# --- Inicialização das Tabelas ---
def init_db():
    """Cria tabelas se não existirem."""
    # Tabela Lojas
    db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS lojas (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(100) UNIQUE NOT NULL,
            responsavel VARCHAR(100) NOT NULL
        );
    """, commit=True)
    
    # Tabela Vendedores
    db_manager.execute_query("""
        CREATE TABLE IF NOT EXISTS vendedores (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            loja_id INTEGER REFERENCES lojas(id) ON DELETE CASCADE,
            loja_nome VARCHAR(100),
            status VARCHAR(20) DEFAULT 'Conectado',
            base_tratada BOOLEAN DEFAULT FALSE,
            disparos_dia INTEGER DEFAULT 0,
            disparos_semanais JSONB DEFAULT '{}',
            ultimo_status_tipo VARCHAR(20),
            ultimo_status_data DATE
        );
    """, commit=True)
    print("Tabelas verificadas/criadas.")

if __name__ == '__main__':
    init_db() # Garante que as tabelas existam ao rodar localmente
    app.run(debug=True)