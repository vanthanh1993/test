function formatNumber(n){
    return n.replace(/\D/g,'')
            .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

document.addEventListener("DOMContentLoaded", function(){

    document.querySelectorAll(".money-input").forEach(input => {

        // format khi load
        let initValue = input.value.replace(/,/g,'')
        if(initValue){
            input.value = formatNumber(initValue)
        }

        // 🔥 khi gõ → KHÔNG format, chỉ giữ số
        input.addEventListener("input", function(e){
            let value = e.target.value.replace(/\D/g,'')
            e.target.value = value
        })

        // format khi rời input
        input.addEventListener("blur", function(e){
            let value = e.target.value.replace(/,/g,'')
            if(value){
                e.target.value = formatNumber(value)
            }
        })

    })

})