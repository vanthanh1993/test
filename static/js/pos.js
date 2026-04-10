let cart = []

// ================= HELPERS =================

// bỏ dấu ,
function getNumber(val){
    return parseInt((val || "").toString().replace(/,/g,'')) || 0
}

// format tiền VNĐ
function formatMoney(n){
    return n.toLocaleString('vi-VN')
}

// ================= UPDATE TOTAL =================
function updateTotal(){
    let price = getNumber(document.getElementById("price").value)
    let qty = cart.length

    let total = price * qty

    document.getElementById("total").innerText = formatMoney(total) + " ₫"
}

// ================= ADD IMEI =================
document.getElementById("imei").addEventListener("keypress", function(e){
    if(e.key === "Enter"){
        let value = e.target.value.trim()
        if(!value) return

        fetch("/api/imei/" + value)
        .then(res => res.json())
        .then(data => {

            if(data.status === "error"){
                alert("❌ Không tìm thấy IMEI")
                return
            }

            if(data.status === "sold"){
                alert("⚠️ IMEI đã bán")
                return
            }

            // tránh trùng
            if(cart.find(i => i.imei === data.imei)){
                alert("⚠️ Đã có trong giỏ")
                return
            }

            cart.push(data)

            renderCart()
            updateTotal()

            e.target.value = ""
        })
    }
})

// ================= RENDER CART =================
function renderCart(){
    let html = ""

    cart.forEach((item, index) => {
        html += `
        <tr>
            <td>${item.imei}</td>
            <td>${item.name}</td>
            <td>
                <button class="btn btn-danger btn-sm" onclick="removeItem(${index})">
                    Xóa
                </button>
            </td>
        </tr>
        `
    })

    document.getElementById("cart").innerHTML = html
}

// ================= REMOVE =================
function removeItem(index){
    cart.splice(index,1)
    renderCart()
    updateTotal()
}

// ================= CHANGE PRICE =================
document.getElementById("price").addEventListener("input", updateTotal)

// ================= PAY =================
function pay(){

    if(cart.length === 0){
        alert("⚠️ Chưa có sản phẩm")
        return
    }

    let price = getNumber(document.getElementById("price").value)
    let paid = getNumber(document.getElementById("paid").value)
    let customer = document.getElementById("customer").value

    if(price <= 0){
        alert("⚠️ Giá bán không hợp lệ")
        return
    }

    fetch("/api/pay",{
        method:"POST",
        headers:{
            "Content-Type":"application/json"
        },
        body:JSON.stringify({
            customer: customer,
            price: price,
            paid: paid,
            cart: cart
        })
    })
    .then(res => res.json())
    .then(data => {
        if(data.ok){
            alert("✅ Thanh toán thành công")

            // reset
            cart = []
            renderCart()
            updateTotal()

            document.getElementById("imei").value = ""
        }else{
            alert("❌ Lỗi thanh toán")
        }
    })
}