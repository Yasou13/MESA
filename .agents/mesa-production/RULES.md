# MESA Üretime Hazırlık Kuralları

1. Amaç MESA’yı temiz, kararlı, güvenli ve canlı ortama hazır hale getirmektir.
2. İlk turda bütün INPUTS dosyalarını ve gerçek kaynak kodu ayrıntılı incele.
3. Rapor bulgularını doğrudan doğru kabul etme; gerçek kod üzerinde doğrula.
4. İlk analiz tamamlanmadan kod değiştirme.
5. Tüm işleri tek `TASKS.md` dosyasına yaz.
6. Aynı anda yalnızca bir görev üzerinde çalış.
7. Büyük işleri küçük, bağımsız ve geri alınabilir parçalara böl.
8. Bilgisayarı zorlayacak işlemleri kontrolsüz başlatma.
9. Tam test paketini her değişiklikten sonra çalıştırma; önce ilgili testleri çalıştır.
10. Ağır testleri en son aşamada ve kontrollü biçimde çalıştır.
11. Değişiklikten önce mevcut davranışı doğrula.
12. Değişiklikten sonra ilgili testleri ve gerekli regresyon testlerini çalıştır.
13. Yapılan düzeltmenin başka bir özelliği bozmadığını doğrula.
14. Hata düzeltirken küçük ve doğrudan ilişkili başka hata bulunursa onu da düzelt.
15. Yeni hata büyük veya riskliyse `TASKS.md` dosyasına görev olarak ekle.
16. Yalnız hata düzeltmekle sınırlı kalma; eksik güvenlik, mimari, doğrulama veya üretim gereksinimlerini görev olarak ekleyebilirsin.
17. Gereksiz refactor ve toplu dosya değişikliği yapma.
18. Dosya silmeden önce import, runtime, test, CI ve dokümantasyon kullanımını kontrol et.
19. Cache, build çıktısı, eski test kalıntısı, geçici dosya ve gerçekten kullanılmayan dosyaları temizle.
20. Yanlış konumlandırılmış dosyaları doğru mimari konuma taşı; importları ve testleri güncelle.
21. Mevcut `.agents/skills/` yapısını koru.
22. Yeni rapor veya uzun hata açıklaması üretme.
23. Görev sonuçları yalnızca `ÇÖZÜLDÜ`, `ÇÖZÜLMEDİ` veya `BLOKLANDI` olsun.
24. Test geçmeden görevi `ÇÖZÜLDÜ` olarak işaretleme.
25. Tüm görevler bitince sistemi yeniden denetle.
26. Yeni açık bulunursa görev listesine ekle ve döngüye devam et.
27. Kritik açık, başarısız test veya eksik üretim gereksinimi varken sistemi canlıya hazır ilan etme.
28. Kullanıcıdan yeni göreve geçmek için onay beklemene gerek yok.
29. Çalışmanı yeni bir github branchı aç ve her görev sonucunda oraya commit + push yap bunun için ikinci bir onay bekleme.
30. Görevler bittiğinde sistem canlıya hazır mı kontrol et değilse neden değil sorgula ve orayı düzeltmeye çalış. 
31. Soak ve benchmar ve benzeri ağır sistemleri çalıştırma eğer canlıya hazır değil kararın sadece bunlardan kaynaklıysa dur başka işlem yapma.
## Durum Güncelleme Kuralları

- Bir görevi yalnız TASKS.md veya STATE.json içinde ÇALIŞIYOR yapmak,
  görevin başlatıldığı veya ilerletildiği anlamına gelmez.
- Bir görev seçildikten sonra aynı tur içinde en az bir gerçek kaynak kodu
  değişikliği veya ilgili doğrulama/test komutu yürütülmelidir.
- Yalnız durum dosyalarını değiştirmek yasaktır.
- Kaynak kodunda işlem yapılmadan nihai cevap üretmek başarısız çalışma sayılır.
- `current_task` değiştirildikten hemen sonra görevin gerçek uygulamasına başlanmalıdır.
- Durum dosyaları yalnız gerçek işlemden sonra güncellenmelidir.
- “Çalışıyor olarak güncellendi”, “incelemeye başlıyorum” veya benzeri ara
  mesajlar nihai cevap olarak gönderilemez.