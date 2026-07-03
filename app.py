# app.py - Maktronic Manufacturing Order Management System
# Multi-department, role-based access with full order workflow
from flask import Flask, request, jsonify, render_template, session, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta
import uuid
from functools import wraps
from sqlalchemy import and_, or_, func
import hashlib

app = Flask(__name__)
app.secret_key = 'maktronic-secret-key-change-in-production'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

CORS(app, supports_credentials=True)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = (
    'mysql+pymysql://root:@localhost/maktronics_order'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'pool_recycle': 3600,
    'pool_pre_ping': True
}

db = SQLAlchemy(app)

# ==================== WORKFLOW CONFIGURATION ====================

STATUS_FLOW = [
    "Order Created",
    "Pending Credit Check",
    "Credit Approved",
    "Amount Pending",
    "Approved for Production",
    "In Processing",
    "Manufacturing Complete",
    "Sent for Quality Check",
    "Quality Approved",
    "Quality Check Failed",
    "Dispatched",
    "Delivered",
    "Cancelled"
]

# Role type constants
ROLE_TYPES = {
    'admin': 'Admin',
    'sales_department': 'Sales Department',
    'accounts_department': 'Accounts Department',
    'partner_manufacturing': 'Partner / Manufacturing',
    'quality_department': 'Quality Department',
}

# What statuses each role can see
ROLE_VISIBLE_STATUSES = {
    'admin': STATUS_FLOW,
    'sales_department': [
        'Order Created', 'Pending Credit Check', 'Credit Approved',
        'Amount Pending', 'Approved for Production', 'In Processing',
        'Manufacturing Complete', 'Sent for Quality Check', 'Quality Approved',
        'Quality Check Failed', 'Dispatched', 'Delivered', 'Cancelled'
    ],
    'accounts_department': [
        'Order Created', 'Pending Credit Check', 'Credit Approved',
        'Amount Pending', 'Approved for Production'
    ],
    'partner_manufacturing': [
        'Approved for Production', 'In Processing',
        'Manufacturing Complete', 'Sent for Quality Check', 'Quality Check Failed'
    ],
    'quality_department': [
        'Sent for Quality Check', 'Quality Approved',
        'Quality Check Failed'
    ],
}

# Valid status transitions per role
ROLE_TRANSITIONS = {
    'admin': {s: STATUS_FLOW for s in STATUS_FLOW},  # Admin can do anything
    'sales_department': {
        'Order Created': ['Pending Credit Check', 'Cancelled'],
        'Quality Approved': ['Dispatched'],
        'Dispatched': ['Delivered'],
    },
    'accounts_department': {
        'Pending Credit Check': ['Credit Approved', 'Amount Pending'],
        'Amount Pending': ['Credit Approved', 'Cancelled'],
        'Credit Approved': ['Approved for Production'],
    },
    'partner_manufacturing': {
        'Approved for Production': ['In Processing'],
        'In Processing': ['Manufacturing Complete'],
        'Manufacturing Complete': ['Sent for Quality Check'],
        'Quality Check Failed': ['In Processing'],
    },
    'quality_department': {
        'Sent for Quality Check': ['Quality Approved', 'Quality Check Failed'],
    },
}


# ==================== MODELS ====================

class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(500))
    contact_email = db.Column(db.String(200))
    contact_phone = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    departments = db.relationship('Department', backref='company', lazy=True, cascade='all, delete-orphan')
    users = db.relationship('User', backref='company', lazy=True)
    orders = db.relationship('Order', backref='company', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'contact_email': self.contact_email,
            'contact_phone': self.contact_phone,
            'is_active': self.is_active,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }


class Department(db.Model):
    __tablename__ = 'departments'

    id = db.Column(db.String(50), primary_key=True)
    company_id = db.Column(db.String(50), db.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    users = db.relationship('User', backref='department', lazy=True)

    def to_dict(self):
        user_count = User.query.filter_by(department_id=self.id).count()
        return {
            'id': self.id,
            'company_id': self.company_id,
            'name': self.name,
            'role_type': self.role_type,
            'role_label': ROLE_TYPES.get(self.role_type, self.role_type),
            'description': self.description,
            'is_active': self.is_active,
            'user_count': user_count,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    company_id = db.Column(db.String(50), db.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    department_id = db.Column(db.String(50), db.ForeignKey('departments.id', ondelete='SET NULL'))
    role = db.Column(db.String(50), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'full_name': self.full_name,
            'email': self.email,
            'phone': self.phone,
            'company_id': self.company_id,
            'department_id': self.department_id,
            'role': self.role,
            'role_label': ROLE_TYPES.get(self.role, self.role),
            'is_active': self.is_active,
            'last_login': self.last_login.strftime('%Y-%m-%d %H:%M:%S') if self.last_login else None,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'company_name': self.company.name if self.company else None,
            'department_name': self.department.name if self.department else 'Admin'
        }


class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.String(50), primary_key=True)
    company_id = db.Column(db.String(50), db.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    client_name = db.Column(db.String(200), nullable=False)
    client_phone = db.Column(db.String(50))
    client_email = db.Column(db.String(200))
    item_description = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, default=0)
    amount_due = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), nullable=False, default='Order Created')
    priority = db.Column(db.String(20), default='normal')  # low, normal, high, urgent
    notes = db.Column(db.Text)
    internal_notes = db.Column(db.Text)
    created_by = db.Column(db.String(200), nullable=False)
    created_by_dept = db.Column(db.String(100))
    taken_by = db.Column(db.String(200))           # Sales person who took the order
    approved_by = db.Column(db.String(200))        # Accounts person who approved credit
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    history = db.relationship('OrderHistory', backref='order', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        balance = self.amount_due - self.amount_paid
        return {
            'id': self.id,
            'company_id': self.company_id,
            'client_name': self.client_name,
            'client_phone': self.client_phone,
            'client_email': self.client_email,
            'item_description': self.item_description,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'amount_due': self.amount_due,
            'amount_paid': self.amount_paid,
            'balance': balance,
            'payment_status': 'Paid' if balance <= 0 else 'Amount Pending' if self.amount_paid > 0 else 'Unpaid',
            'status': self.status,
            'priority': self.priority,
            'notes': self.notes,
            'internal_notes': self.internal_notes,
            'created_by': self.created_by,
            'created_by_dept': self.created_by_dept,
            'taken_by': self.taken_by,
            'approved_by': self.approved_by,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }


class OrderHistory(db.Model):
    __tablename__ = 'order_history'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(db.String(50), db.ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    note = db.Column(db.Text)
    changed_by = db.Column(db.String(200), nullable=False)
    changed_by_dept = db.Column(db.String(100))
    changed_by_role = db.Column(db.String(50))
    payment_mode = db.Column(db.String(100))   # e.g. Cash, NEFT, Cheque, UPI
    payment_ref = db.Column(db.String(200))    # Payment reference / transaction ID
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'status': self.status,
            'note': self.note,
            'changed_by': self.changed_by,
            'changed_by_dept': self.changed_by_dept,
            'changed_by_role': self.changed_by_role,
            'payment_mode': self.payment_mode,
            'payment_ref': self.payment_ref,
            'role_label': ROLE_TYPES.get(self.changed_by_role, self.changed_by_role),
            'changed_at': self.changed_at.strftime('%Y-%m-%d %H:%M:%S') if self.changed_at else None
        }


# ==================== AUTH DECORATOR ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        if session.get('user_role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


# ==================== HELPER ====================

def now():
    return datetime.utcnow()


def init_db():
    db.create_all()

    # Safe migration: add new columns if they don't exist (for existing databases)
    from sqlalchemy import text
    with db.engine.connect() as conn:
        # Orders table new columns
        for col, defn in [
            ('taken_by', 'VARCHAR(200)'),
            ('approved_by', 'VARCHAR(200)'),
        ]:
            try:
                conn.execute(text(f'ALTER TABLE orders ADD COLUMN {col} {defn}'))
                conn.commit()
            except Exception:
                pass  # Column already exists

        # Order history table new columns
        for col, defn in [
            ('payment_mode', 'VARCHAR(100)'),
            ('payment_ref', 'VARCHAR(200)'),
        ]:
            try:
                conn.execute(text(f'ALTER TABLE order_history ADD COLUMN {col} {defn}'))
                conn.commit()
            except Exception:
                pass  # Column already exists

    existing = Company.query.filter_by(id="COMP-MAKTRONIC").first()
    if existing:
        return

    # Company
    company = Company(
        id="COMP-MAKTRONIC",
        name="Maktronic Manufacturing",
        address="Plot 12, Industrial Area, Phase II",
        contact_email="info@maktronic.com",
        contact_phone="+91-9876543210",
        created_at=now()
    )
    db.session.add(company)
    db.session.flush()

    # Departments
    depts = [
        Department(id="DEPT-SALES", company_id=company.id, name="Sales Department",
                   role_type="sales_department", description="Handles client orders and dispatch", created_at=now()),
        Department(id="DEPT-ACCOUNTS", company_id=company.id, name="Accounts Department",
                   role_type="accounts_department", description="Credit checks and payment tracking", created_at=now()),
        Department(id="DEPT-MFG", company_id=company.id, name="Partner Manufacturing",
                   role_type="partner_manufacturing", description="Production and manufacturing", created_at=now()),
        Department(id="DEPT-QC", company_id=company.id, name="Quality Department",
                   role_type="quality_department", description="Quality assurance and control", created_at=now()),
    ]
    for d in depts:
        db.session.add(d)
    db.session.flush()

    # Users
    users = [
        User(username='admin', password='admin123', full_name='System Administrator',
             email='admin@maktronic.com', company_id=company.id, department_id=None,
             role='admin', created_at=now()),
        User(username='sales_user', password='sales123', full_name='Rajesh Kumar',
             email='rajesh@maktronic.com', company_id=company.id, department_id='DEPT-SALES',
             role='sales_department', created_at=now()),
        User(username='accounts_user', password='accounts123', full_name='Priya Sharma',
             email='priya@maktronic.com', company_id=company.id, department_id='DEPT-ACCOUNTS',
             role='accounts_department', created_at=now()),
        User(username='manufacturing_user', password='manufacturing123', full_name='Suresh Patel',
             email='suresh@maktronic.com', company_id=company.id, department_id='DEPT-MFG',
             role='partner_manufacturing', created_at=now()),
        User(username='quality_user', password='quality123', full_name='Anita Singh',
             email='anita@maktronic.com', company_id=company.id, department_id='DEPT-QC',
             role='quality_department', created_at=now()),
    ]
    for u in users:
        db.session.add(u)

    # Sample orders
    sample_statuses = [
        ("Order Created", 0, 0),
        ("Pending Credit Check", 1500, 0),
        ("Amount Pending", 2000, 500),
        ("Credit Approved", 3000, 3000),
        ("Approved for Production", 4500, 4500),
        ("In Processing", 2500, 2500),
        ("Manufacturing Complete", 1800, 1800),
        ("Sent for Quality Check", 3200, 3200),
        ("Quality Approved", 2800, 2800),
        ("Dispatched", 1200, 1200),
    ]
    clients = ["Alpha Corp", "Beta Industries", "Gamma Ltd", "Delta Systems", "Epsilon Tech",
               "Zeta Works", "Eta Manufacturing", "Theta Exports", "Iota Enterprises", "Kappa Solutions"]
    items = ["Steel Frame Assembly", "PCB Circuit Board x100", "Hydraulic Pump Unit", "Control Panel Module",
             "Conveyor Belt System", "Gearbox Set", "Electric Motor 5HP", "Pressure Valve Kit",
             "Sensor Array Module", "Relay Control Box"]

    for i, (status, due, paid) in enumerate(sample_statuses):
        order = Order(
            id=f"ORD-SAMPLE{i+1:03d}",
            company_id=company.id,
            client_name=clients[i],
            client_phone=f"98765{43210+i}",
            client_email=f"client{i+1}@example.com",
            item_description=items[i],
            quantity=i + 1,
            unit_price=due / (i + 1) if due > 0 else 0,
            amount_due=due,
            amount_paid=paid,
            status=status,
            priority=['normal', 'high', 'urgent', 'low', 'normal', 'high', 'normal', 'urgent', 'low', 'normal'][i],
            created_by="Rajesh Kumar",
            created_by_dept="Sales Department",
            created_at=now() - timedelta(days=10 - i),
            updated_at=now() - timedelta(days=5 - min(i, 4))
        )
        db.session.add(order)

        history = OrderHistory(
            order_id=order.id,
            status=status,
            note=f"Sample order initialized at status: {status}",
            changed_by="System",
            changed_by_dept="System",
            changed_by_role="admin",
            changed_at=now()
        )
        db.session.add(history)

    db.session.commit()
    print("✅ Database initialized with Maktronic Manufacturing data")


# ==================== AUTH ROUTES ====================

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(
        username=data.get('username'),
        password=data.get('password'),
        is_active=True
    ).first()

    if user:
        session.permanent = True
        session['user_id'] = user.id
        session['username'] = user.username
        session['user_role'] = user.role
        session['company_id'] = user.company_id
        session['department_id'] = user.department_id
        session['full_name'] = user.full_name

        # Update last login
        user.last_login = now()
        db.session.commit()

        return jsonify({'success': True, 'user': user.to_dict()})
    return jsonify({'success': False, 'error': 'Invalid credentials or account inactive'}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user and user.is_active:
            return jsonify({'authenticated': True, 'user': user.to_dict()})
    return jsonify({'authenticated': False}), 401


# ==================== COMPANY ROUTES ====================

@app.route('/api/companies', methods=['GET'])
@login_required
def get_companies():
    if session.get('user_role') != 'admin':
        # Non-admins only see their own company
        company = Company.query.get(session.get('company_id'))
        return jsonify([company.to_dict()] if company else [])
    companies = Company.query.order_by(Company.created_at.desc()).all()
    return jsonify([c.to_dict() for c in companies])


@app.route('/api/companies', methods=['POST'])
@admin_required
def create_company():
    data = request.json
    company_id = 'COMP-' + str(uuid.uuid4())[:8].upper()
    company = Company(
        id=company_id,
        name=data['name'],
        address=data.get('address', ''),
        contact_email=data.get('contact_email', ''),
        contact_phone=data.get('contact_phone', ''),
        created_at=now()
    )
    db.session.add(company)
    db.session.commit()
    return jsonify({'success': True, 'company_id': company_id, 'company': company.to_dict()})


@app.route('/api/companies/<company_id>', methods=['PUT'])
@admin_required
def update_company(company_id):
    company = Company.query.get(company_id)
    if not company:
        return jsonify({'error': 'Not found'}), 404
    data = request.json
    for field in ['name', 'address', 'contact_email', 'contact_phone', 'is_active']:
        if field in data:
            setattr(company, field, data[field])
    db.session.commit()
    return jsonify({'success': True, 'company': company.to_dict()})


# ==================== DEPARTMENT ROUTES ====================

@app.route('/api/departments', methods=['POST'])
@admin_required
def create_department():
    data = request.json
    # Validate company belongs to admin's scope
    dept_id = 'DEPT-' + str(uuid.uuid4())[:8].upper()
    department = Department(
        id=dept_id,
        company_id=data['company_id'],
        name=data['name'],
        role_type=data['role_type'],
        description=data.get('description', ''),
        created_at=now()
    )
    db.session.add(department)
    db.session.commit()
    return jsonify({'success': True, 'department_id': dept_id, 'department': department.to_dict()})


@app.route('/api/departments/<company_id>', methods=['GET'])
@login_required
def get_departments(company_id):
    departments = Department.query.filter_by(company_id=company_id).order_by(Department.created_at).all()
    return jsonify([d.to_dict() for d in departments])


@app.route('/api/departments/detail/<dept_id>', methods=['PUT'])
@admin_required
def update_department(dept_id):
    dept = Department.query.get(dept_id)
    if not dept:
        return jsonify({'error': 'Not found'}), 404
    data = request.json
    for field in ['name', 'role_type', 'description', 'is_active']:
        if field in data:
            setattr(dept, field, data[field])
    db.session.commit()
    return jsonify({'success': True, 'department': dept.to_dict()})


@app.route('/api/departments/detail/<dept_id>', methods=['DELETE'])
@admin_required
def delete_department(dept_id):
    dept = Department.query.get(dept_id)
    if not dept:
        return jsonify({'error': 'Not found'}), 404
    # Check if dept has users
    user_count = User.query.filter_by(department_id=dept_id).count()
    if user_count > 0:
        return jsonify({'error': f'Cannot delete department with {user_count} active users. Reassign users first.'}), 400
    db.session.delete(dept)
    db.session.commit()
    return jsonify({'success': True})


# ==================== USER ROUTES ====================

@app.route('/api/users', methods=['POST'])
@admin_required
def create_user():
    data = request.json

    # Check username unique
    existing = User.query.filter_by(username=data['username']).first()
    if existing:
        return jsonify({'error': 'Username already exists'}), 400

    user = User(
        username=data['username'],
        password=data['password'],
        full_name=data['full_name'],
        email=data.get('email', ''),
        phone=data.get('phone', ''),
        company_id=data['company_id'],
        department_id=data.get('department_id') or None,
        role=data['role'],
        is_active=True,
        created_at=now()
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({'success': True, 'user': user.to_dict()})


@app.route('/api/users/<company_id>', methods=['GET'])
@login_required
def get_users(company_id):
    if session.get('user_role') != 'admin' and session.get('company_id') != company_id:
        return jsonify({'error': 'Unauthorized'}), 403
    users = User.query.filter_by(company_id=company_id).order_by(User.created_at).all()
    return jsonify([u.to_dict() for u in users])


@app.route('/api/users/detail/<int:user_id>', methods=['PUT'])
@admin_required
def update_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Not found'}), 404
    data = request.json
    for field in ['full_name', 'email', 'phone', 'department_id', 'role', 'is_active']:
        if field in data:
            setattr(user, field, data[field] or None if field == 'department_id' else data[field])
    if 'password' in data and data['password']:
        user.password = data['password']
    db.session.commit()
    return jsonify({'success': True, 'user': user.to_dict()})


@app.route('/api/users/detail/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'Not found'}), 404
    if user.id == session.get('user_id'):
        return jsonify({'error': 'Cannot delete your own account'}), 400
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True})


# ==================== ORDER ROUTES ====================

@app.route('/api/orders', methods=['GET'])
@login_required
def get_orders():
    company_id = request.args.get('company_id') or session.get('company_id')
    role = session.get('user_role')
    status_filter = request.args.get('status')
    priority_filter = request.args.get('priority')
    search = request.args.get('search', '').strip()

    query = Order.query.filter_by(company_id=company_id)

    # Filter by role visibility
    if role != 'admin':
        visible = ROLE_VISIBLE_STATUSES.get(role, [])
        query = query.filter(Order.status.in_(visible))

    if status_filter:
        query = query.filter_by(status=status_filter)

    if priority_filter:
        query = query.filter_by(priority=priority_filter)

    if search:
        query = query.filter(
            or_(
                Order.client_name.ilike(f'%{search}%'),
                Order.id.ilike(f'%{search}%'),
                Order.item_description.ilike(f'%{search}%')
            )
        )

    orders = query.order_by(Order.created_at.desc()).all()
    return jsonify([o.to_dict() for o in orders])


@app.route('/api/orders', methods=['POST'])
@login_required
def create_order():
    role = session.get('user_role')
    if role not in ['admin', 'sales_department']:
        return jsonify({'error': 'Only Sales or Admin can create orders'}), 403

    data = request.json
    order_id = 'ORD-' + str(uuid.uuid4())[:8].upper()

    quantity = int(data.get('quantity', 1))
    unit_price = float(data.get('unit_price', 0))
    amount_due = float(data.get('amount_due', unit_price * quantity))

    order = Order(
        id=order_id,
        company_id=data['company_id'],
        client_name=data['client_name'],
        client_phone=data.get('client_phone', ''),
        client_email=data.get('client_email', ''),
        item_description=data['item_description'],
        quantity=quantity,
        unit_price=unit_price,
        amount_due=amount_due,
        amount_paid=0,
        status='Order Created',
        priority=data.get('priority', 'normal'),
        notes=data.get('notes', ''),
        created_by=session.get('full_name', 'Unknown'),
        created_by_dept=data.get('dept_name', 'Sales Department'),
        taken_by=data.get('taken_by', ''),
        created_at=now(),
        updated_at=now()
    )
    db.session.add(order)

    history = OrderHistory(
        order_id=order_id,
        status='Order Created',
        note=f"Order created for {data['client_name']}",
        changed_by=session.get('full_name', 'Unknown'),
        changed_by_dept=data.get('dept_name', 'Sales Department'),
        changed_by_role=role,
        changed_at=now()
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({'success': True, 'order_id': order_id, 'order': order.to_dict()})


@app.route('/api/orders/<order_id>', methods=['GET'])
@login_required
def get_order(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({'error': 'Not found'}), 404

    if order.company_id != session.get('company_id') and session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    history = OrderHistory.query.filter_by(order_id=order_id).order_by(OrderHistory.changed_at.asc()).all()
    return jsonify({'order': order.to_dict(), 'history': [h.to_dict() for h in history]})


@app.route('/api/orders/<order_id>/status', methods=['PUT'])
@login_required
def update_status(order_id):
    data = request.json
    new_status = data.get('status')
    role = session.get('user_role')

    if new_status not in STATUS_FLOW:
        return jsonify({'error': 'Invalid status'}), 400

    order = Order.query.get(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    if order.company_id != session.get('company_id') and role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    current_status = order.status

    # Check role transition permission
    if role != 'admin':
        allowed = ROLE_TRANSITIONS.get(role, {}).get(current_status, [])
        if new_status not in allowed:
            return jsonify({'error': f'Your role cannot transition from "{current_status}" to "{new_status}"'}), 403

    # Update payment if provided
    amount_paid = data.get('amount_paid')
    if amount_paid is not None:
        order.amount_paid += float(amount_paid)

    order.status = new_status
    order.updated_at = now()

    # Add internal notes if provided
    if data.get('internal_notes'):
        order.internal_notes = (order.internal_notes or '') + f"\n[{now().strftime('%Y-%m-%d')}] {data['internal_notes']}"

    # Add history
    history = OrderHistory(
        order_id=order_id,
        status=new_status,
        note=data.get('note', ''),
        changed_by=session.get('full_name', 'Unknown'),
        changed_by_dept=data.get('dept_name', ''),
        changed_by_role=role,
        changed_at=now()
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({'success': True, 'order': order.to_dict()})


@app.route('/api/orders/<order_id>/credit-check', methods=['POST'])
@login_required
def process_credit_check(order_id):
    role = session.get('user_role')
    if role not in ['admin', 'accounts_department']:
        return jsonify({'error': 'Only Accounts Department can process credit checks'}), 403

    data = request.json
    payment_amount = float(data.get('payment_amount', 0))
    decision = data.get('decision')
    note = data.get('note', '')

    order = Order.query.get(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    # Allow processing for both initial credit check AND follow-up payment on Amount Pending
    allowed_statuses = ['Pending Credit Check', 'Amount Pending']
    if order.status not in allowed_statuses:
        return jsonify({'error': f'This action is only available for orders in credit check or amount pending state. Current status: {order.status}'}), 400

    if payment_amount > 0:
        order.amount_paid += payment_amount

    balance = order.amount_due - order.amount_paid

    if decision == 'approved':
        if balance <= 0:
            new_status = 'Credit Approved'
            status_note = f'Full payment received. Total paid: ₹{order.amount_paid:,.2f}'
        else:
            new_status = 'Amount Pending'
            status_note = f'Partial payment recorded. Paid: ₹{order.amount_paid:,.2f}, Balance still due: ₹{balance:,.2f}'
    elif decision == 'pending':
        new_status = 'Amount Pending'
        status_note = f'Marked as amount pending. Due: ₹{order.amount_due:,.2f}, Paid so far: ₹{order.amount_paid:,.2f}'
    else:
        return jsonify({'error': 'Invalid decision. Use: approved or pending'}), 400

    order.status = new_status
    order.updated_at = now()

    # Save approved_by on the order when credit is fully approved
    approved_by_name = data.get('approved_by', '')
    if approved_by_name:
        order.approved_by = approved_by_name

    full_note = f"{status_note}. Accounts Note: {note}" if note else status_note
    if data.get('payment_mode'):
        full_note += f" | Mode: {data['payment_mode']}"
    if data.get('payment_ref'):
        full_note += f" | Ref: {data['payment_ref']}"

    history = OrderHistory(
        order_id=order_id,
        status=new_status,
        note=full_note,
        changed_by=approved_by_name or session.get('full_name', 'Unknown'),
        changed_by_dept='Accounts Department',
        changed_by_role=role,
        payment_mode=data.get('payment_mode', ''),
        payment_ref=data.get('payment_ref', ''),
        changed_at=now()
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({
        'success': True,
        'new_status': new_status,
        'amount_paid': order.amount_paid,
        'balance': balance
    })


@app.route('/api/orders/<order_id>', methods=['DELETE'])
@admin_required
def delete_order(order_id):
    order = Order.query.get(order_id)
    if order:
        db.session.delete(order)
        db.session.commit()
    return jsonify({'success': True})


# ==================== STATS ROUTES ====================

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    company_id = request.args.get('company_id') or session.get('company_id')
    role = session.get('user_role')

    stats = {}

    if role == 'admin':
        total = Order.query.filter_by(company_id=company_id).count()
        by_status_result = db.session.query(
            Order.status, func.count(Order.id)
        ).filter_by(company_id=company_id).group_by(Order.status).all()
        by_status = {s: c for s, c in by_status_result}

        total_revenue = db.session.query(func.sum(Order.amount_due)).filter_by(company_id=company_id).scalar() or 0
        total_paid = db.session.query(func.sum(Order.amount_paid)).filter_by(company_id=company_id).scalar() or 0
        dept_count = Department.query.filter_by(company_id=company_id).count()
        user_count = User.query.filter_by(company_id=company_id).count()

        stats = {
            'total_orders': total,
            'by_status': by_status,
            'total_revenue': total_revenue,
            'total_paid': total_paid,
            'outstanding': total_revenue - total_paid,
            'dept_count': dept_count,
            'user_count': user_count,
        }

    elif role == 'sales_department':
        total = Order.query.filter_by(company_id=company_id).count()
        new_orders = Order.query.filter_by(company_id=company_id, status='Order Created').count()
        dispatched = Order.query.filter_by(company_id=company_id, status='Dispatched').count()
        quality_ready = Order.query.filter_by(company_id=company_id, status='Quality Approved').count()
        stats = {
            'total_orders': total,
            'new_orders': new_orders,
            'quality_approved': quality_ready,
            'dispatched': dispatched,
        }

    elif role == 'accounts_department':
        pending_credit = Order.query.filter_by(company_id=company_id, status='Pending Credit Check').count()
        amount_pending_count = Order.query.filter_by(company_id=company_id, status='Amount Pending').count()
        approved = Order.query.filter_by(company_id=company_id, status='Credit Approved').count()

        total_pending_amount = db.session.query(
            func.sum(Order.amount_due - Order.amount_paid)
        ).filter(Order.company_id == company_id, Order.status == 'Amount Pending').scalar() or 0

        total_collected = db.session.query(func.sum(Order.amount_paid)).filter_by(company_id=company_id).scalar() or 0
        stats = {
            'pending_credit_check': pending_credit,
            'amount_pending_count': amount_pending_count,
            'credit_approved': approved,
            'total_pending_amount': total_pending_amount,
            'total_collected': total_collected,
        }

    elif role == 'partner_manufacturing':
        approved_for_prod = Order.query.filter_by(company_id=company_id, status='Approved for Production').count()
        in_processing = Order.query.filter_by(company_id=company_id, status='In Processing').count()
        complete = Order.query.filter_by(company_id=company_id, status='Manufacturing Complete').count()
        in_qc = Order.query.filter_by(company_id=company_id, status='Sent for Quality Check').count()
        qc_failed = Order.query.filter_by(company_id=company_id, status='Quality Check Failed').count()
        stats = {
            'approved_for_production': approved_for_prod,
            'in_processing': in_processing,
            'manufacturing_complete': complete,
            'in_quality_check': in_qc,
            'quality_check_failed': qc_failed,
        }

    elif role == 'quality_department':
        pending_qc = Order.query.filter_by(company_id=company_id, status='Sent for Quality Check').count()
        approved = Order.query.filter_by(company_id=company_id, status='Quality Approved').count()
        failed = Order.query.filter_by(company_id=company_id, status='Quality Check Failed').count()
        stats = {
            'pending_quality_check': pending_qc,
            'quality_approved': approved,
            'quality_failed': failed,
        }

    return jsonify(stats)


@app.route('/api/statuses', methods=['GET'])
@login_required
def get_statuses():
    role = session.get('user_role', 'admin')
    visible = ROLE_VISIBLE_STATUSES.get(role, STATUS_FLOW)
    transitions = ROLE_TRANSITIONS.get(role, {})
    return jsonify({
        'all': STATUS_FLOW,
        'visible': visible,
        'transitions': transitions,
        'role': role
    })


@app.route('/api/role-types', methods=['GET'])
@login_required
def get_role_types():
    return jsonify(ROLE_TYPES)


# ==================== FRONTEND ====================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/new-order', methods=['GET', 'POST'])
def new_order_page():
    # Must be logged in
    if 'user_id' not in session:
        return redirect('/')

    role = session.get('user_role')
    if role not in ['admin', 'sales_department']:
        return redirect('/')

    if request.method == 'POST':
        # Parse form fields
        action = request.form.get('action', 'order')   # 'draft' or 'order'
        company_name = request.form.get('company', '').strip()
        phone        = request.form.get('phone', '').strip()
        email        = request.form.get('email', '').strip()
        address      = request.form.get('address', '').strip()
        gst          = request.form.get('gst', '').strip()
        pan          = request.form.get('pan', '').strip()
        taken_by     = request.form.get('taken_by', '').strip()
        notes        = request.form.get('notes', '').strip()
        internal_notes = request.form.get('internal_notes', '').strip()
        priority     = request.form.get('priority', 'normal')
        order_date   = request.form.get('order_date', now().strftime('%Y-%m-%d'))
        payment_terms = request.form.get('payment_terms', '')

        # Collect line items (multi-value fields)
        particulars = request.form.getlist('particular')
        qtys        = request.form.getlist('qty')
        rates       = request.form.getlist('rate')

        # Build item description and compute totals
        items = []
        subtotal = 0.0
        for p, q, r in zip(particulars, qtys, rates):
            p = p.strip()
            q = float(q) if q else 1
            r = float(r) if r else 0
            if p:
                items.append(f"{p} (Qty: {int(q)}, Rate: ₹{r:,.2f})")
                subtotal += q * r

        gst_amount  = subtotal * 0.18   # 9% CGST + 9% SGST
        grand_total = subtotal + gst_amount

        item_description = '; '.join(items) if items else 'No items specified'

        # Determine quantity and unit price from first item
        first_qty   = float(qtys[0])  if qtys  else 1
        first_rate  = float(rates[0]) if rates else 0

        order_id = 'ORD-' + str(uuid.uuid4())[:8].upper()

        order = Order(
            id=order_id,
            company_id=session.get('company_id'),
            client_name=company_name or 'Unknown Client',
            client_phone=phone,
            client_email=email,
            item_description=item_description,
            quantity=int(first_qty),
            unit_price=first_rate,
            amount_due=round(grand_total, 2),
            amount_paid=0,
            status='Order Created',
            priority=priority,
            notes=(notes + (f'\nPayment Terms: {payment_terms}' if payment_terms else '')).strip(),
            internal_notes=internal_notes,
            created_by=session.get('full_name', 'Unknown'),
            created_by_dept='Sales Department',
            taken_by=taken_by,
            created_at=now(),
            updated_at=now()
        )
        db.session.add(order)

        history = OrderHistory(
            order_id=order_id,
            status='Order Created',
            note=f"Order created via order form for {company_name}. Action: {action}.",
            changed_by=session.get('full_name', 'Unknown'),
            changed_by_dept='Sales Department',
            changed_by_role=role,
            changed_at=now()
        )
        db.session.add(history)
        db.session.commit()

        # Redirect back to the main app
        return redirect('/')

    # GET — render the form
    from datetime import date as date_cls
    today     = date_cls.today().strftime('%Y-%m-%d')
    order_id  = 'ORD-' + str(uuid.uuid4())[:8].upper()

    # Fetch clients (Company records) for the dropdown
    clients = Company.query.filter_by(is_active=True).all()

    return render_template('new_order.html',
                           order_id=order_id,
                           today=today,
                           clients=clients)


# ==================== MAIN ====================

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, port=5002)
