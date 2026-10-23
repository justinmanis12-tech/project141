// Silme/kaldırma işlemlerinde onay iste (CSP nedeniyle satır içi onclick yok)
document.addEventListener('submit', function (e) {
  var msg = e.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) e.preventDefault();
});
