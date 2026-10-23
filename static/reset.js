// Supabase şifre sıfırlama bağlantısı jetonu URL parçasında (#access_token=...) gönderir.
(function () {
  var p = new URLSearchParams((location.hash || '').replace(/^#/, ''));
  var t = p.get('access_token');
  var f = document.getElementById('reset-form');
  var warn = document.getElementById('reset-missing');
  if (t && f) {
    f.elements['access_token'].value = t;
    f.hidden = false;
    history.replaceState(null, '', location.pathname);
  } else if (warn) {
    warn.hidden = false;
  }
})();
