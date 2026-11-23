/* Requires the official CryptoPro browser plug-in and cadesplugin_api.js.
   No private key material is transferred to the server. */
(async function(){
 const root=document.getElementById("ukep-widget");
 if(!root)return;
 const id=root.dataset.agreementId,csrf=root.dataset.csrf;
 const state=root.querySelector("#ukep-status"),select=root.querySelector("#ukep-cert");
 const button=root.querySelector("#ukep-sign");
 const show=(s)=>{state.textContent=s};
 const plugin=window.cadesplugin;
 if(!plugin){show("КриптоПро ЭЦП Browser plug-in не обнаружен. Установите официальное расширение и компонент cadesplugin_api.js.");return;}
 try{
  await plugin;
  const store=await plugin.CreateObjectAsync("CAdESCOM.Store");
  await store.Open(2,"My",2);
  const certificates=await store.Certificates;
  const count=await certificates.Count;
  const items=[];
  for(let i=1;i<=count;i++){
    const cert=await certificates.Item(i);
    const subject=await cert.SubjectName;
    const thumb=await cert.Thumbprint;
    items.push(cert);
    const option=document.createElement("option");option.value=String(items.length-1);
    option.textContent=subject+" ["+thumb.slice(-12)+"]";select.appendChild(option);
  }
  await store.Close();
  if(!items.length){show("Доступные сертификаты не найдены");return;}
  button.disabled=false;show("Выберите сертификат и проверьте реквизиты подписанта.");
  button.addEventListener("click",async()=>{
    button.disabled=true;
    try{
      show("Получаем окончательную PDF-версию договора…");
      const response=await fetch("/agreements/"+id+"/ukep/document",{credentials:"same-origin",cache:"no-store"});
      if(!response.ok)throw Error("PDF недоступен: "+response.status);
      const bytes=new Uint8Array(await response.arrayBuffer());
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)))
          .map(x=>x.toString(16).padStart(2,"0")).join("");
      let binary="";for(let i=0;i<bytes.length;i+=8192)
        binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      const base64=btoa(binary);
      const signer=await plugin.CreateObjectAsync("CAdESCOM.CPSigner");
      await signer.propset_Certificate(items[Number(select.value)]);
      await signer.propset_CheckCertificate(true);
      const signed=await plugin.CreateObjectAsync("CAdESCOM.CadesSignedData");
      await signed.propset_ContentEncoding(1);
      await signed.propset_Content(base64);
      show("Подтвердите операцию подписи в КриптоПро…");
      const signature=await signed.SignCades(signer,1,true);
      const form=new FormData();form.set("csrf_token",csrf);
      form.set("signature_base64",signature);form.set("document_sha256",digest);
      show("Проверяем УКЭП на сервере…");
      const result=await fetch("/agreements/"+id+"/ukep/sign",
          {method:"POST",body:form,credentials:"same-origin"});
      const data=await result.json();
      if(!result.ok)throw Error(data.detail||"Сервер не подтвердил подпись");
      show("Подпись подтверждена. Обновляем карточку…");
      location.reload();
    }catch(err){show("Подписание не завершено: "+String(err.message||err));button.disabled=false;}
  });
 }catch(err){show("Ошибка компонента КриптоПро: "+String(err.message||err));}
})();