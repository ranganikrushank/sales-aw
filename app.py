import os
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

SERVICE_KEYS = ['website_development', 'ecommerce_website', 'custom_software', 'app_development', 'ai_software']
SERVICE_LABELS = {
    'website_development': 'Website Development',
    'ecommerce_website': 'E Commerce Website',
    'custom_software': 'Custom Software Development',
    'app_development': 'App Development',
    'ai_software': 'AI Software & AI Agents'
}

def login_required(role):
    def decorator(f):
        def decorated(*args, **kwargs):
            if 'user_id' not in session or session.get('role') != role:
                flash("Unauthorized access.")
                return redirect(url_for('admin_login') if role == 'admin' else 'sales_login')
            return f(*args, **kwargs)
        decorated.__name__ = f.__name__
        return decorated
    return decorator

def get_current_month():
    return datetime.now().strftime("%Y-%m")

# --- Auth ---
@app.route('/')
def index(): return redirect(url_for('sales_login'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        res = supabase.table('users').select('*').eq('username', request.form['username']).eq('role', 'admin').execute()
        if res.data and check_password_hash(res.data[0]['password_hash'], request.form['password']):
            session.update({'user_id': res.data[0]['id'], 'role': 'admin', 'username': res.data[0]['username']})
            return redirect(url_for('admin_dashboard'))
        flash('Invalid Admin Credentials')
    return render_template('admin_login.html')

@app.route('/sales/login', methods=['GET', 'POST'])
def sales_login():
    if request.method == 'POST':
        res = supabase.table('users').select('*').eq('username', request.form['username']).eq('role', 'sales').execute()
        if res.data and check_password_hash(res.data[0]['password_hash'], request.form['password']):
            session.update({'user_id': res.data[0]['id'], 'role': 'sales', 'username': res.data[0]['username']})
            return redirect(url_for('sales_dashboard'))
        flash('Invalid Sales Credentials')
    return render_template('sales_login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- Sales Routes ---
@app.route('/sales/dashboard')
@login_required('sales')
def sales_dashboard():

    uid = session['user_id']

    # Leads
    leads = supabase.table('leads') \
        .select('*') \
        .eq('sales_id', uid) \
        .order('created_at', desc=True) \
        .execute().data

    # Clients
    payable_clients = supabase.table('clients') \
        .select(
            'custom_client_id, total_amount, commission_amount, service_selected, is_payment_cleared, is_commission_paid'
        ) \
        .eq('sales_id', uid) \
        .execute().data

    payable = [
        c for c in payable_clients
        if c['is_payment_cleared'] and c['is_commission_paid']
    ]

    pending = [
        c for c in payable_clients
        if not c['is_payment_cleared'] or not c['is_commission_paid']
    ]

    # Totals
    total_commission = sum(
        c['commission_amount'] or 0
        for c in payable
    )

    total_revenue = sum(
        c['total_amount'] or 0
        for c in payable
    )

    # User Data
    user = supabase.table('users') \
        .select('base_salary, commission_config') \
        .eq('id', uid) \
        .execute().data[0]

    comm_config = user.get(
        'commission_config',
        {
            'indian': {},
            'foreign': {}
        }
    )

    # DAILY TASKS
    today = date.today().isoformat()

    daily_tasks = supabase.table('daily_tasks') \
        .select('*') \
        .eq('sales_id', uid) \
        .lte('task_date', today) \
        .order('task_date', desc=False) \
        .execute().data

    return render_template(

        'sales_dashboard.html',

        leads=leads,

        clients=payable,

        pending_clients=pending,

        total_commission=total_commission,

        total_revenue=total_revenue,

        current_salary=user['base_salary'],

        comm_config=comm_config,

        service_labels=SERVICE_LABELS,

        daily_tasks=daily_tasks

    )

@app.route('/admin/tasks')
@login_required('admin')
def admin_tasks():

    sales_users = supabase.table('users') \
        .select('id, username') \
        .eq('role', 'sales') \
        .execute().data

    tasks = supabase.table('daily_tasks') \
        .select('id, sales_id, task_title, task_description, priority, task_date, is_completed, incomplete_reason, incomplete_submitted, incomplete_submitted_at, users!daily_tasks_sales_id_fkey(username)') \
        .order('created_at', desc=True) \
        .execute().data

    return render_template(
        'admin_tasks.html',
        sales_users=sales_users,
        tasks=tasks
    )
    
@app.route('/sales/tasks')
@login_required('sales')
def sales_tasks():

    uid = session['user_id']

    today = date.today().isoformat()
    
    current_hour = datetime.now().hour

    allow_reason_submission = current_hour >= 22

    tasks = supabase.table('daily_tasks') \
        .select('*') \
        .eq('sales_id', uid) \
        .lte('task_date', today) \
        .order('task_date', desc=False) \
        .execute().data

    return render_template(

        'sales_tasks.html',

        tasks=tasks,

        allow_reason_submission=allow_reason_submission

    )

@app.route('/sales/incomplete_task/<task_id>', methods=['POST'])
@login_required('sales')
def incomplete_task(task_id):

    uid = session['user_id']

    task_res = supabase.table('daily_tasks') \
        .select('*') \
        .eq('id', task_id) \
        .eq('sales_id', uid) \
        .execute()

    if not task_res.data:

        flash('Task not found.')

        return redirect(url_for('sales_tasks'))

    current_hour = datetime.now().hour

    # ALLOW ONLY AFTER 10 PM
    if not (current_hour >= 22 or current_hour < 2):

        flash(
            'Reason submission allowed only after 10 PM.'
        )

        return redirect(url_for('sales_tasks'))

    reason = request.form['incomplete_reason']

    if not reason.strip():

        flash('Reason is required.')

        return redirect(url_for('sales_tasks'))

    supabase.table('daily_tasks').update({

        'incomplete_reason': reason,

        'incomplete_submitted': True,

        'incomplete_submitted_at': datetime.now().isoformat()

    }).eq('id', task_id).execute()

    flash('Incomplete task reason submitted.')

    return redirect(url_for('sales_tasks'))
    
@app.route('/sales/complete_task/<task_id>')
@login_required('sales')
def complete_task(task_id):

    uid = session['user_id']

    task_res = supabase.table('daily_tasks') \
        .select('*') \
        .eq('id', task_id) \
        .eq('sales_id', uid) \
        .execute()

    if not task_res.data:
        flash('Task not found.')
        return redirect(url_for('sales_tasks'))

    task = task_res.data[0]

    today = date.today().isoformat()

    if task['task_date'] > today:
        flash('Future tasks cannot be completed yet.')
        return redirect(url_for('sales_tasks'))

    supabase.table('daily_tasks').update({
        'is_completed': True
    }).eq('id', task_id).execute()

    flash('Task marked as completed.')

    return redirect(url_for('sales_tasks'))

@app.route('/admin/edit_task/<task_id>', methods=['GET', 'POST'])
@login_required('admin')
def edit_task(task_id):

    task_res = supabase.table('daily_tasks') \
        .select('*') \
        .eq('id', task_id) \
        .execute()

    if not task_res.data:
        flash('Task not found.')
        return redirect(url_for('admin_tasks'))

    task = task_res.data[0]

    # BLOCK EDIT IF COMPLETED
    if task['is_completed']:
        flash('Completed tasks cannot be edited.')
        return redirect(url_for('admin_tasks'))

    sales_users = supabase.table('users') \
        .select('id, username') \
        .eq('role', 'sales') \
        .execute().data

    if request.method == 'POST':

        supabase.table('daily_tasks').update({

            'sales_id': request.form['sales_id'],

            'task_title': request.form['task_title'],

            'task_description': request.form['task_description'],

            'priority': request.form['priority'],

            'task_date': request.form['task_date']

        }).eq('id', task_id).execute()

        flash('Task updated successfully.')

        return redirect(url_for('admin_tasks'))

    return render_template(
        'edit_task.html',
        task=task,
        sales_users=sales_users
    )
@app.route('/sales/add_lead', methods=['GET', 'POST'])
@login_required('sales')
def sales_add_lead():
    if request.method == 'POST':
        services = request.form.getlist('services')
        supabase.table('leads').insert([{
            'sales_id': session['user_id'], 'company_name': request.form['company_name'],
            'email': request.form['email'], 'phone': request.form['phone'],
            'platform': request.form['platform'], 'country': request.form['country'],
            'services': services, 'sector': request.form['sector'],
            'follow_up_date': request.form['follow_up_date'] or None
        }]).execute()
        flash('Lead added successfully!')
        return redirect(url_for('sales_dashboard'))
    return render_template('sales_add_lead.html')

@app.route('/sales/convert/<lead_id>')
@login_required('sales')
def convert_lead(lead_id):
    supabase.table('leads').update({'is_converted': True}).eq('id', lead_id).execute()
    flash('Lead marked for conversion.')
    return redirect(url_for('sales_dashboard'))

# --- Admin Routes ---
@app.route('/admin/dashboard')
@login_required('admin')
def admin_dashboard():
    sales_users = supabase.table('users').select('*').eq('role', 'sales').execute().data
    
    client_ids = [c['lead_id'] for c in supabase.table('clients').select('lead_id').execute().data]
    pending_leads = supabase.table('leads').select('id, sales_id, company_name, sector, users!leads_sales_id_fkey(username)') \
                    .eq('is_converted', True).not_.in_('id', client_ids).execute().data
    
    all_leads = supabase.table('leads').select('*, users!leads_sales_id_fkey(username)').order('created_at', desc=True).execute().data
    all_clients = supabase.table('clients').select('*, users!clients_sales_id_fkey(username)') \
                  .order('created_at', desc=True).execute().data
    
    return render_template('admin_dashboard.html', sales_users=sales_users, pending_leads=pending_leads,
                           all_leads=all_leads, all_clients=all_clients, current_month=get_current_month(),
                           service_keys=SERVICE_KEYS, service_labels=SERVICE_LABELS)

@app.route('/admin/sales_details/<sales_id>')
@login_required('admin')
def sales_details(sales_id):
    user = supabase.table('users').select('*').eq('id', sales_id).execute().data[0]
    leads = supabase.table('leads').select('*').eq('sales_id', sales_id).order('created_at', desc=True).execute().data
    clients = supabase.table('clients').select('*').eq('sales_id', sales_id).order('created_at', desc=True).execute().data
    return render_template('sales_details.html', user=user, leads=leads, clients=clients, 
                           service_labels=SERVICE_LABELS)

@app.route('/admin/create_sales', methods=['POST'])
@login_required('admin')
def create_sales():

    comm_config = {
        'indian': {},
        'foreign': {}
    }

    for region in ['indian', 'foreign']:

        for key in SERVICE_KEYS:

            value = request.form.get(f'{region}_{key}')

            comm_config[region][key] = float(value) if value else 0

    supabase.table('users').insert({

        'username': request.form['username'],

        'password_hash': generate_password_hash(
            request.form['password']
        ),

        'role': 'sales',

        'base_salary': float(
            request.form['salary'] or 0
        ),

        'commission_config': comm_config

    }).execute()

    flash(
        f'Sales account created for {request.form["username"]}'
    )

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/edit_sales/<sales_id>', methods=['GET', 'POST'])
@login_required('admin')
def edit_sales(sales_id):

    user_res = supabase.table('users') \
        .select('*') \
        .eq('id', sales_id) \
        .eq('role', 'sales') \
        .execute()

    if not user_res.data:
        flash('Sales account not found')
        return redirect(url_for('admin_dashboard'))

    user = user_res.data[0]

    if request.method == 'POST':

        comm_config = {
            'indian': {},
            'foreign': {}
        }

        for region in ['indian', 'foreign']:

            for key in SERVICE_KEYS:

                value = request.form.get(f'{region}_{key}')

                comm_config[region][key] = float(value) if value else 0

        update_data = {
            'username': request.form['username'],
            'base_salary': float(request.form['salary'] or 0),
            'commission_config': comm_config
        }

        # OPTIONAL PASSWORD UPDATE
        password = request.form.get('password')

        if password:
            update_data['password_hash'] = generate_password_hash(password)

        supabase.table('users') \
            .update(update_data) \
            .eq('id', sales_id) \
            .execute()

        flash('Sales account updated successfully.')

        return redirect(url_for('admin_dashboard'))

    return render_template(
        'edit_sales.html',
        user=user,
        service_labels=SERVICE_LABELS
    )
@app.route('/admin/assign_commission', methods=['POST'])
@login_required('admin')
def assign_commission():
    lead_id, sales_id = request.form['lead_id'], request.form['sales_id']
    total_amount = float(request.form['total_amount'])
    service_key, region = request.form['service'], request.form['region']
    advance = float(request.form.get('advance_payment', 0))
    
    user_res = supabase.table('users') \
    .select('username, commission_config') \
    .eq('id', sales_id) \
    .execute()

    user_data = user_res.data[0]

    sales_username = user_data['username']

    config = user_data.get('commission_config', {
        'indian': {},
        'foreign': {}
    })

    # -------- AUTO CLIENT ID GENERATION --------

    existing_clients = supabase.table('clients') \
        .select('custom_client_id') \
        .eq('sales_id', sales_id) \
        .execute()

    client_count = len(existing_clients.data) + 1

    custom_client_id = f"{sales_username}_{client_count}"
    comm_pct = config.get(region, {}).get(service_key, 0)
    comm_amt = total_amount * (comm_pct / 100)
    left = total_amount - advance
    is_cleared = (left <= 0)
    
    supabase.table('clients').insert([{
        'custom_client_id': custom_client_id,
        'lead_id': lead_id,
        'sales_id': sales_id,
        'total_amount': total_amount,
        'commission_percent': comm_pct, 'commission_amount': comm_amt,
        'service_selected': SERVICE_LABELS.get(service_key, service_key), 'client_region': region,
        'advance_payment': advance, 'payment_left': left, 'is_payment_cleared': is_cleared,
        'is_commission_paid': False # Default to unpaid until admin marks it
    }]).execute()
    flash('Commission assigned. Payment tracking enabled.')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_client/<client_id>', methods=['POST'])
@login_required('admin')
def update_client(client_id):
    action = request.form.get('action')
    if action == 'mark_payment':
        supabase.table('clients').update({'is_payment_cleared': True, 'payment_left': 0}).eq('id', client_id).execute()
        flash('Client payment marked as fully cleared.')
    elif action == 'toggle_commission':
        client = supabase.table('clients').select('is_commission_paid').eq('id', client_id).execute().data[0]
        supabase.table('clients').update({'is_commission_paid': not client['is_commission_paid']}).eq('id', client_id).execute()
        flash('Commission payout status toggled.')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/update_salary', methods=['POST'])
@login_required('admin')
def update_salary():
    sales_id, new_sal = request.form['sales_id'], float(request.form['new_salary'])
    last_sal = supabase.table('salaries').select('is_paid').eq('sales_id', sales_id).order('created_at', desc=True).limit(1).execute().data
    if last_sal and not last_sal[0]['is_paid']:
        flash('Cannot update salary. Previous month not marked as paid.')
        return redirect(url_for('admin_dashboard'))

    supabase.table('users').update({'base_salary': new_sal}).eq('id', sales_id).execute()
    
    # INSERT historical record for tracking
    supabase.table('salaries').insert({
        'sales_id': sales_id,
        'amount': new_sal,
        'month': datetime.now().strftime("%Y-%m"),
        'is_paid': False
    }).execute()

    flash('Base salary updated & history logged.')
    return redirect(url_for('admin_dashboard'))

# --- SALES PANEL: Personal History ---
@app.route('/sales/history')
@login_required('sales')
def sales_history():
    uid = session['user_id']
    
    # Commission History (only paid commissions)
    comm_history = supabase.table('clients').select(
        'id, custom_client_id, total_amount, commission_amount, commission_percent, service_selected, client_region, created_at'
    ) \
        .eq('sales_id', uid).eq('is_commission_paid', True).order('created_at', desc=True).execute().data
        
    # Salary History
    sal_history = supabase.table('salaries').select('*').eq('sales_id', uid).order('created_at', desc=True).execute().data
    
    return render_template('sales_history.html', comm_history=comm_history, sal_history=sal_history)

# --- ADMIN PANEL: View Any Salesperson's History ---
@app.route('/admin/sales_history/<sales_id>')
@login_required('admin')
def admin_sales_history(sales_id):
    user = supabase.table('users').select('id, username, base_salary').eq('id', sales_id).eq('role', 'sales').execute().data
    if not user:
        flash("Sales user not found.")
        return redirect(url_for('admin_dashboard'))
    user = user[0]
    
    comm_history = supabase.table('clients').select(
        'id, custom_client_id, total_amount, commission_amount, commission_percent, service_selected, client_region, created_at'
    ) \
        .eq('sales_id', sales_id).eq('is_commission_paid', True).order('created_at', desc=True).execute().data
        
    sal_history = supabase.table('salaries').select('*').eq('sales_id', sales_id).order('created_at', desc=True).execute().data
    
    return render_template('admin_sales_history.html', user=user, comm_history=comm_history, sal_history=sal_history)

@app.route('/admin/mark_paid/<sal_id>')
@login_required('admin')
def mark_paid(sal_id):
    supabase.table('salaries').update({'is_paid': True}).eq('id', sal_id).execute()
    flash('Salary marked as paid.')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_lead/<lead_id>')
@login_required('admin')
def delete_lead(lead_id):
    supabase.table('clients').delete().eq('lead_id', lead_id).execute()
    supabase.table('leads').delete().eq('id', lead_id).execute()
    flash('Lead deleted.')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/client_details/<client_id>')
@login_required('admin')
def client_details(client_id):

    client_res = supabase.table('clients') \
        .select('*') \
        .eq('id', client_id) \
        .execute()

    if not client_res.data:
        flash('Client not found')
        return redirect(url_for('admin_dashboard'))

    client = client_res.data[0]

    lead = supabase.table('leads') \
        .select('*') \
        .eq('id', client['lead_id']) \
        .execute().data[0]

    sales = supabase.table('users') \
        .select('username') \
        .eq('id', client['sales_id']) \
        .execute().data[0]

    return render_template(
        'client_details.html',
        client=client,
        lead=lead,
        sales=sales
    )

@app.route('/admin/lead_details/<lead_id>')
@login_required('admin')
def admin_lead_details(lead_id):

    lead_res = supabase.table('leads') \
        .select('*') \
        .eq('id', lead_id) \
        .execute()

    if not lead_res.data:
        flash('Lead not found')
        return redirect(url_for('admin_dashboard'))

    lead = lead_res.data[0]

    sales = supabase.table('users') \
        .select('username') \
        .eq('id', lead['sales_id']) \
        .execute().data[0]

    followups = supabase.table('lead_followups') \
        .select('*') \
        .eq('lead_id', lead_id) \
        .order('created_at', desc=True) \
        .execute().data

    return render_template(
        'admin_lead_details.html',
        lead=lead,
        sales=sales,
        followups=followups
    )
        
@app.route('/sales/edit_lead/<lead_id>', methods=['GET', 'POST'])
@login_required('sales')
def edit_lead(lead_id):

    lead_res = supabase.table('leads') \
        .select('*') \
        .eq('id', lead_id) \
        .execute()

    if not lead_res.data:
        flash('Lead not found')
        return redirect(url_for('sales_dashboard'))

    lead = lead_res.data[0]

    if request.method == 'POST':

        services = request.form.getlist('services')

        supabase.table('leads').update({
            'company_name': request.form['company_name'],
            'email': request.form['email'],
            'phone': request.form['phone'],
            'platform': request.form['platform'],
            'country': request.form['country'],
            'sector': request.form['sector'],
            'services': services,
            'follow_up_date': request.form['follow_up_date'],
            'follow_up_notes': request.form['follow_up_notes']
        }).eq('id', lead_id).execute()

        flash('Lead updated successfully')
        return redirect(url_for('sales_dashboard'))

    return render_template('edit_lead.html', lead=lead)

@app.route('/sales/lead_details/<lead_id>')
@login_required('sales')
def lead_details(lead_id):

    lead_res = supabase.table('leads') \
        .select('*') \
        .eq('id', lead_id) \
        .execute()

    if not lead_res.data:
        flash('Lead not found')
        return redirect(url_for('sales_dashboard'))

    lead = lead_res.data[0]

    followups = supabase.table('lead_followups') \
    .select('*') \
    .eq('lead_id', lead_id) \
    .order('created_at', desc=True) \
    .execute().data

    return render_template(
        'lead_details.html',
        lead=lead,
        followups=followups
    )

@app.route('/sales/add_followup/<lead_id>', methods=['POST'])
@login_required('sales')
def add_followup(lead_id):

    supabase.table('lead_followups').insert({
        'lead_id': lead_id,
        'sales_id': session['user_id'],
        'followup_note': request.form['followup_note'],
        'followup_date': request.form['followup_date'],
        'is_done': False
    }).execute()

    flash('Follow up added successfully.')

    return redirect(url_for('lead_details', lead_id=lead_id))

@app.route('/sales/complete_followup/<followup_id>/<lead_id>')
@login_required('sales')
def complete_followup(followup_id, lead_id):

    supabase.table('lead_followups').update({
        'is_done': True
    }).eq('id', followup_id).execute()

    flash('Follow up marked as completed.')

    return redirect(url_for('lead_details', lead_id=lead_id))

@app.route('/admin/add_daily_task', methods=['POST'])
@login_required('admin')
def add_daily_task():

    supabase.table('daily_tasks').insert({

        'sales_id': request.form['sales_id'],

        'task_title': request.form['task_title'],

        'task_description': request.form['task_description'],

        'priority': request.form['priority'],

        'task_date': request.form['task_date'],

        'is_completed': False

    }).execute()

    flash('Daily task assigned successfully.')

    return redirect(url_for('admin_tasks'))

# ================================
# SALES REPORTS MODULE
# ================================

@app.route('/sales/reports')
@login_required('sales')
def sales_reports():

    uid = session['user_id']

    reports = supabase.table('sales_daily_reports') \
        .select('*, users!sales_daily_reports_sales_id_fkey(username)') \
        .eq('sales_id', uid) \
        .order('created_at', desc=True) \
        .execute().data

    return render_template(
        'sales_reports.html',
        reports=reports
    )


@app.route('/sales/add_report', methods=['GET', 'POST'])
@login_required('sales')
def add_report():

    if request.method == 'POST':

        supabase.table('sales_daily_reports').insert({

            'sales_id': session['user_id'],

            'total_calls': int(
                request.form['total_calls']
            ),

            'total_emails': int(
                request.form['total_emails']
            ),

            'total_whatsapp': int(
                request.form['total_whatsapp']
            ),

            'positive_responses': int(
                request.form['positive_responses']
            ),

            'negative_responses': int(
                request.form['negative_responses']
            ),

            'clients_converted': int(
                request.form['clients_converted']
            ),

            'leads_lost': int(
                request.form['leads_lost']
            ),

            'lost_reason': request.form['lost_reason'],

            'converted_reason': request.form['converted_reason']

        }).execute()

        flash('Daily sales report submitted successfully.')

        return redirect(url_for('sales_reports'))

    return render_template(
        'add_report.html',
        today_date=date.today().isoformat()
    )


@app.route('/sales/edit_report/<report_id>', methods=['GET', 'POST'])
@login_required('sales')
def edit_report(report_id):

    report_res = supabase.table('sales_reports') \
        .select('*') \
        .eq('id', report_id) \
        .execute()

    if not report_res.data:
        flash('Report not found.')
        return redirect(url_for('sales_reports'))

    report = report_res.data[0]

    if request.method == 'POST':

        supabase.table('sales_reports').update({

            'task_name': request.form['task_name'],

            'task_description': request.form['task_description'],

            'client_name': request.form['client_name'],

            'lead_source': request.form['lead_source'],

            'meeting_type': request.form['meeting_type'],

            'work_status': request.form['work_status'],

            'priority': request.form['priority'],

            'time_spent': request.form['time_spent'],

            'outcome': request.form['outcome'],

            'next_followup_date': request.form['next_followup_date'] or None,

            'potential_amount': float(
                request.form['potential_amount'] or 0
            ),

            'location': request.form['location']

        }).eq('id', report_id).execute()

        flash('Report updated successfully.')

        return redirect(url_for('sales_reports'))

    return render_template(
        'edit_report.html',
        report=report
    )


@app.route('/sales/delete_report/<report_id>')
@login_required('sales')
def delete_report(report_id):

    supabase.table('sales_reports') \
        .delete() \
        .eq('id', report_id) \
        .execute()

    flash('Report deleted successfully.')

    return redirect(url_for('sales_reports'))


@app.route('/admin/reports')
@login_required('admin')
def admin_reports():

    selected_sales = request.args.get('sales_person')

    selected_date = request.args.get('date')

    # GET ALL SALES USERS
    sales_users = supabase.table('users') \
        .select('id, username') \
        .eq('role', 'sales') \
        .execute().data

    # BASE QUERY
    query = supabase.table('sales_daily_reports') \
        .select('*, users!sales_daily_reports_sales_id_fkey(username)')

    # FILTER BY SALES PERSON
    if selected_sales:
        query = query.eq('sales_id', selected_sales)

    # FILTER BY DATE
    if selected_date:
        query = query.eq('report_date', selected_date)

    # FINAL REPORTS
    reports = query \
        .order('report_date', desc=True) \
        .execute().data

    return render_template(

        'admin_reports.html',

        reports=reports,

        sales_users=sales_users

    )

    
if __name__ == '__main__':
    app.run(debug=True)