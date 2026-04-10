from flask import Flask, render_template, request, redirect, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import os

app = Flask(__name__)
app.secret_key = 'secret123'

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30)
)

try:
    init_db()
except Exception as e:
    print("DB init error:", e)
        
# ===== DB =====
def get_conn():
    return psycopg2.connect(
        os.environ.get("DATABASE_URL"),
        sslmode='require'
    )

def query_one(sql, params=()):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(sql, params)
        return c.fetchone()
    finally:
        conn.close()

def query_all(sql, params=()):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(sql, params)
        return c.fetchall()
    finally:
        conn.close()

def execute(sql, params=()):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(sql, params)
        conn.commit()
    finally:
        conn.close()

# ===== HELPER =====
def now_vn():
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def parse_money(val):
    return int((val or "0").replace('.', '').replace(',', '').strip())

def safe_sum(row):
    if not row:
        return 0
    if 'sum' in row and row['sum']:
        return row['sum']
    return 0

# ===== AUTH =====
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect('/')
        return f(*args, **kwargs)
    return wrapper

# ===== FILTER =====
@app.template_filter('vnd')
def vnd(value):
    try:
        return f"{int(value):,}".replace(",", ".") + " ₫"
    except Exception:
        return "0 ₫"

# ===== INIT DB =====
def init_db():
    execute("""
        CREATE TABLE IF NOT EXISTS users(
            id SERIAL PRIMARY KEY,
            username TEXT,
            password TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS suppliers(
            id SERIAL PRIMARY KEY,
            name TEXT,
            phone TEXT,
            address TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS supplier_payments(
            id SERIAL PRIMARY KEY,
            supplier_id INT,
            amount INT,
            date TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS import_logs(
            id SERIAL PRIMARY KEY,
            name TEXT,
            price INT,
            qty INT,
            total INT,
            date TEXT,
            supplier_id INT,
            paid INT DEFAULT 0,
            debt INT DEFAULT 0
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS imei(
            imei TEXT PRIMARY KEY,
            product_name TEXT,
            import_price INT,
            sell_price INT,
            status TEXT,
            import_date TEXT,
            sell_date TEXT,
            customer_id INT,
            import_id INT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS customers(
            id SERIAL PRIMARY KEY,
            name TEXT,
            phone TEXT,
            address TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS sales_orders(
            id SERIAL PRIMARY KEY,
            date TEXT,
            customer_id INT,
            total INT,
            paid INT,
            debt INT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS sales_items(
            id SERIAL PRIMARY KEY,
            order_id INT,
            imei TEXT,
            price INT,
            import_price INT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS debt_payments(
            id SERIAL PRIMARY KEY,
            customer_id INT,
            amount INT,
            date TEXT
        )
    """)

    if not query_one("SELECT * FROM users LIMIT 1"):
        execute(
            "INSERT INTO users(username,password) VALUES(%s,%s)",
            ('admin', generate_password_hash('123456'))
        )

# ===== LOGIN =====
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect('/dashboard')

    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']

        user = query_one("SELECT * FROM users WHERE username=%s", (u,))
        if user and check_password_hash(user['password'], p):
            session['user'] = u
            return redirect('/dashboard')

        flash("Sai tài khoản hoặc mật khẩu", "error")

    return render_template("login.html")

# ===== DASHBOARD =====
@app.route('/dashboard')
@login_required
def dashboard():
    stock = query_one("SELECT COUNT(*) AS count FROM imei WHERE status='in_stock'")
    revenue = query_one("SELECT COALESCE(SUM(total), 0) AS sum FROM sales_orders")
    profit = query_one("SELECT COALESCE(SUM(price - import_price), 0) AS sum FROM sales_items")
    debt = query_one("SELECT COALESCE(SUM(debt), 0) AS sum FROM import_logs")

    return render_template(
        "dashboard.html",
        stock=stock['count'] if stock else 0,
        revenue_total=safe_sum(revenue),
        profit=safe_sum(profit),
        debt_supplier=safe_sum(debt)
    )

# ===== IMPORT =====
@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_goods():
    if request.method == 'POST':
        name = request.form['name']
        price = parse_money(request.form['price'])
        supplier_id = request.form['supplier_id']
        imeis = list(set(request.form['imeis'].split()))

        if not imeis:
            flash("Thiếu IMEI", "error")
            return redirect('/import')

        conn = get_conn()
        try:
            c = conn.cursor()

            c.execute("""
                INSERT INTO import_logs(name, price, qty, total, date, supplier_id, paid, debt)
                VALUES (%s, %s, 0, 0, %s, %s, 0, 0)
                RETURNING id
            """, (name, price, now_vn(), supplier_id))

            import_id = c.fetchone()['id']
            success = 0

            for im in imeis:
                try:
                    c.execute("""
                        INSERT INTO imei(
                            imei, product_name, import_price, sell_price, status,
                            import_date, sell_date, customer_id, import_id
                        )
                        VALUES (%s, %s, %s, 0, 'in_stock', %s, '', NULL, %s)
                    """, (im, name, price, now_vn(), import_id))
                    success += 1
                except psycopg2.Error:
                    conn.rollback()
                    c = conn.cursor()

                    c.execute("""
                        SELECT COUNT(*) FROM import_logs WHERE id=%s
                    """, (import_id,))

            total = success * price
            c.execute("""
                UPDATE import_logs
                SET qty=%s, total=%s, debt=%s
                WHERE id=%s
            """, (success, total, total, import_id))

            conn.commit()
        finally:
            conn.close()

        flash(f"Nhập {success} máy", "success")
        return redirect('/import')

    return render_template(
        "import.html",
        imports=query_all("SELECT * FROM import_logs ORDER BY id DESC"),
        suppliers=query_all("SELECT * FROM suppliers")
    )

# ===== DELETE IMPORT =====
@app.route('/delete/<int:id>')
@login_required
def delete_import(id):
    sold_exists = query_one(
        "SELECT 1 FROM imei WHERE import_id=%s AND status!='in_stock'",
        (id,)
    )
    if sold_exists:
        flash("Đã bán không xoá", "error")
        return redirect('/import')

    execute("DELETE FROM imei WHERE import_id=%s", (id,))
    execute("DELETE FROM import_logs WHERE id=%s", (id,))

    flash("Đã xoá", "success")
    return redirect('/import')

# ===== EDIT IMPORT =====
@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_import(id):
    item = query_one("SELECT * FROM import_logs WHERE id=%s", (id,))
    if not item:
        flash("Không tìm thấy phiếu nhập", "error")
        return redirect('/import')

    old = [i['imei'] for i in query_all("SELECT imei FROM imei WHERE import_id=%s", (id,))]

    if request.method == 'POST':
        name = request.form['name']
        price = parse_money(request.form['price'])
        new = list(set(request.form['imeis'].split()))

        removed = set(old) - set(new)
        added = set(new) - set(old)

        conn = get_conn()
        try:
            c = conn.cursor()

            for im in removed:
                r = query_one("SELECT status FROM imei WHERE imei=%s", (im,))
                if r and r['status'] != 'in_stock':
                    flash(f"{im} đã bán", "error")
                    return redirect(f"/edit/{id}")
                c.execute("DELETE FROM imei WHERE imei=%s", (im,))

            for im in added:
                try:
                    c.execute("""
                        INSERT INTO imei(
                            imei, product_name, import_price, sell_price, status,
                            import_date, sell_date, customer_id, import_id
                        )
                        VALUES (%s, %s, %s, 0, 'in_stock', %s, '', NULL, %s)
                    """, (im, name, price, now_vn(), id))
                except psycopg2.Error:
                    conn.rollback()
                    c = conn.cursor()

            qty = len(new)
            total = qty * price

            c.execute("""
                UPDATE imei
                SET product_name=%s, import_price=%s
                WHERE import_id=%s
            """, (name, price, id))

            c.execute("""
                UPDATE import_logs
                SET name=%s, price=%s, qty=%s, total=%s, debt=%s
                WHERE id=%s
            """, (name, price, qty, total, total, id))

            conn.commit()
        finally:
            conn.close()

        return redirect('/import')

    return render_template("edit_import.html", item=item, imeis="\n".join(old))

# ===== POS =====
@app.route('/pos')
@login_required
def pos():
    customers = query_all("SELECT * FROM customers")
    return render_template("pos.html", customers=customers)

# ===== CUSTOMERS =====
@app.route('/customers', methods=['GET', 'POST'])
@login_required
def customers():
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        address = request.form['address']

        if not name.strip():
            flash("Tên không được để trống", "error")
            return redirect('/customers')

        execute(
            "INSERT INTO customers(name, phone, address) VALUES (%s, %s, %s)",
            (name, phone, address)
        )
        return redirect('/customers')

    data = query_all("SELECT * FROM customers")
    return render_template("customers.html", customers=data)

# ===== CUSTOMER DETAIL =====
@app.route('/customer/<int:id>')
@login_required
def customer_detail(id):
    customer = query_one("SELECT * FROM customers WHERE id=%s", (id,))
    if not customer:
        return "Không tìm thấy khách hàng"

    orders_raw = query_all("""
        SELECT * FROM sales_orders
        WHERE customer_id=%s
        ORDER BY id DESC
    """, (id,))

    orders = []
    total_debt = 0

    for o in orders_raw:
        imeis = query_all("SELECT imei FROM sales_items WHERE order_id=%s", (o['id'],))
        imei_list = [i['imei'] for i in imeis]
        total_debt += o['debt'] or 0

        orders.append({
            "id": o['id'],
            "date": o['date'],
            "total": o['total'],
            "paid": o['paid'],
            "debt": o['debt'],
            "imeis": imei_list
        })

    payments = query_all("""
        SELECT * FROM debt_payments
        WHERE customer_id=%s
        ORDER BY id DESC
    """, (id,))

    return render_template(
        "customer_detail.html",
        customer=customer,
        orders=orders,
        payments=payments,
        debt=total_debt
    )

# ===== STOCK =====
@app.route('/stock')
@login_required
def stock():
    rows = query_all("""
        SELECT product_name, COUNT(*) AS qty, SUM(import_price) AS total
        FROM imei
        WHERE status='in_stock'
        GROUP BY product_name
    """)

    data = []
    total_all = 0

    for r in rows:
        imeis = query_all("""
            SELECT imei
            FROM imei
            WHERE product_name=%s AND status='in_stock'
        """, (r['product_name'],))

        imei_list = [i['imei'] for i in imeis]
        total_all += r['total'] or 0

        data.append({
            "product_name": r['product_name'],
            "qty": r['qty'],
            "total": r['total'],
            "imeis": imei_list
        })

    return render_template("stock.html", data=data, total_all=total_all)

# ===== API =====
@app.route('/api/imei/<imei>')
def api_imei(imei):
    row = query_one("SELECT * FROM imei WHERE imei=%s", (imei,))

    if not row:
        return jsonify({"status": "error"})

    if row['status'] != 'in_stock':
        return jsonify({"status": "sold"})

    return jsonify({
        "status": "ok",
        "name": row['product_name']
    })

# ===== SUPPLIERS =====
@app.route('/suppliers', methods=['GET', 'POST'])
@login_required
def suppliers():
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        address = request.form['address']

        if not name.strip():
            flash("Tên không được để trống", "error")
            return redirect('/suppliers')

        execute(
            "INSERT INTO suppliers(name, phone, address) VALUES (%s, %s, %s)",
            (name, phone, address)
        )
        return redirect('/suppliers')

    data = query_all("SELECT * FROM suppliers")
    return render_template("suppliers.html", suppliers=data)

# ===== SUPPLIER DETAIL =====
@app.route('/supplier/<int:id>')
@login_required
def supplier_detail(id):
    supplier = query_one("SELECT * FROM suppliers WHERE id=%s", (id,))
    if not supplier:
        return "Không tìm thấy nhà cung cấp"

    imports_raw = query_all("""
        SELECT * FROM import_logs
        WHERE supplier_id=%s
        ORDER BY id DESC
    """, (id,))

    imports = []
    for i in imports_raw:
        imeis = query_all("SELECT imei FROM imei WHERE import_id=%s", (i['id'],))
        imports.append({
            "id": i['id'],
            "date": i['date'],
            "total": i['total'],
            "imeis": [x['imei'] for x in imeis]
        })

    payments = query_all("""
        SELECT * FROM supplier_payments
        WHERE supplier_id=%s
        ORDER BY id DESC
    """, (id,))

    total_import = sum(i['total'] or 0 for i in imports_raw)
    total_paid = sum(p['amount'] or 0 for p in payments)
    debt = total_import - total_paid

    return render_template(
        "supplier_detail.html",
        supplier=supplier,
        imports=imports,
        payments=payments,
        debt=debt
    )

# ===== EDIT SUPPLIER =====
@app.route('/edit_supplier/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_supplier(id):
    supplier = query_one("SELECT * FROM suppliers WHERE id=%s", (id,))
    if not supplier:
        return "Không tìm thấy"

    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        address = request.form['address']

        if not name.strip():
            flash("Tên không hợp lệ", "error")
            return redirect(f"/edit_supplier/{id}")

        execute("""
            UPDATE suppliers
            SET name=%s, phone=%s, address=%s
            WHERE id=%s
        """, (name, phone, address, id))

        return redirect('/suppliers')

    return render_template("edit_supplier.html", supplier=supplier)

# ===== DELETE SUPPLIER =====
@app.route('/delete_supplier/<int:id>')
@login_required
def delete_supplier(id):
    has_import = query_one("SELECT 1 FROM import_logs WHERE supplier_id=%s", (id,))
    has_payment = query_one("SELECT 1 FROM supplier_payments WHERE supplier_id=%s", (id,))

    if has_import or has_payment:
        flash("Có lịch sử, không xoá", "error")
        return redirect('/suppliers')

    execute("DELETE FROM suppliers WHERE id=%s", (id,))
    return redirect('/suppliers')

# ===== PAY SUPPLIER =====
@app.route('/supplier/pay', methods=['POST'])
@login_required
def supplier_pay():
    supplier_id = request.form['supplier_id']
    amount = parse_money(request.form['amount'])

    if amount <= 0:
        flash("Số tiền không hợp lệ", "error")
        return redirect(f"/supplier/{supplier_id}")

    conn = get_conn()
    try:
        c = conn.cursor()

        c.execute("""
            SELECT * FROM import_logs
            WHERE supplier_id=%s AND debt > 0
            ORDER BY id
        """, (supplier_id,))
        rows = c.fetchall()

        if not rows:
            flash("Không có công nợ", "error")
            return redirect(f"/supplier/{supplier_id}")

        original_amount = amount

        c.execute("""
            INSERT INTO supplier_payments(supplier_id, amount, date)
            VALUES (%s, %s, %s)
        """, (supplier_id, original_amount, now_vn()))

        for r in rows:
            if amount <= 0:
                break

            pay = min(amount, r['debt'])

            c.execute("""
                UPDATE import_logs
                SET debt = debt - %s,
                    paid = paid + %s
                WHERE id=%s
            """, (pay, pay, r['id']))

            amount -= pay

        conn.commit()
    finally:
        conn.close()

    flash("Thanh toán thành công", "success")
    return redirect(f"/supplier/{supplier_id}")

# ===== PAY CUSTOMER =====
@app.route('/pay_debt/<int:id>', methods=['POST'])
@login_required
def pay_debt(id):
    amount = parse_money(request.form['amount'])

    if amount <= 0:
        flash("Số tiền không hợp lệ", "error")
        return redirect(f"/customer/{id}")

    conn = get_conn()
    try:
        c = conn.cursor()

        c.execute("""
            SELECT * FROM sales_orders
            WHERE customer_id=%s AND debt > 0
            ORDER BY id
        """, (id,))
        orders = c.fetchall()

        if not orders:
            flash("Không có công nợ", "error")
            return redirect(f"/customer/{id}")

        original_amount = amount

        c.execute("""
            INSERT INTO debt_payments(customer_id, amount, date)
            VALUES (%s, %s, %s)
        """, (id, original_amount, now_vn()))

        for o in orders:
            if amount <= 0:
                break

            pay = min(amount, o['debt'])

            c.execute("""
                UPDATE sales_orders
                SET paid = paid + %s,
                    debt = debt - %s
                WHERE id=%s
            """, (pay, pay, o['id']))

            amount -= pay

        conn.commit()
    finally:
        conn.close()

    flash("Thanh toán thành công", "success")
    return redirect(f"/customer/{id}")

# ===== LOGOUT =====
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ===== RUN =====
if __name__ == '__main__':
    try:
        init_db()
    except Exception as e:
        print("DB init error:", e)