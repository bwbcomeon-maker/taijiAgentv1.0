(function(){
  'use strict';

  var LEGACY_STORAGE_KEY_PREFIX = 'her' + 'mes-';
  var TAIJI_STORAGE_KEY_PREFIX = 'taiji-';
  var PATCH_FLAG = '__taijiStoragePatched';

  function mapStorageKey(key){
    if(typeof key !== 'string') return key;
    if(key.indexOf(LEGACY_STORAGE_KEY_PREFIX) === 0){
      return TAIJI_STORAGE_KEY_PREFIX + key.slice(LEGACY_STORAGE_KEY_PREFIX.length);
    }
    return key;
  }

  function migrateLegacyStorage(storage){
    if(!storage) return;
    var legacyKeys = [];
    try{
      for(var i = 0; i < storage.length; i += 1){
        var key = storage.key(i);
        if(typeof key === 'string' && key.indexOf(LEGACY_STORAGE_KEY_PREFIX) === 0){
          legacyKeys.push(key);
        }
      }
      legacyKeys.forEach(function(legacyKey){
        var nextKey = mapStorageKey(legacyKey);
        if(storage.getItem(nextKey) === null){
          var legacyValue = storage.getItem(legacyKey);
          if(legacyValue !== null) storage.setItem(nextKey, legacyValue);
        }
        storage.removeItem(legacyKey);
      });
    }catch(_){}
  }

  function patchStoragePrototype(){
    var proto = window.Storage && window.Storage.prototype;
    if(!proto || proto[PATCH_FLAG]) return;

    var originalGetItem = proto.getItem;
    var originalSetItem = proto.setItem;
    var originalRemoveItem = proto.removeItem;

    proto.getItem = function(key){
      return originalGetItem.call(this, mapStorageKey(key));
    };
    proto.setItem = function(key, value){
      return originalSetItem.call(this, mapStorageKey(key), value);
    };
    proto.removeItem = function(key){
      var mapped = mapStorageKey(key);
      originalRemoveItem.call(this, mapped);
      if(mapped !== key) originalRemoveItem.call(this, key);
    };

    Object.defineProperty(proto, PATCH_FLAG, {value: true});
  }

  function productKey(name){
    return TAIJI_STORAGE_KEY_PREFIX + String(name || '');
  }

  function legacyKey(name){
    return LEGACY_STORAGE_KEY_PREFIX + String(name || '');
  }

  window.TAIJI_STORAGE_KEY_PREFIX = TAIJI_STORAGE_KEY_PREFIX;
  window.__taijiMapStorageKey = mapStorageKey;
  window.__taijiStoreGet = window.__taijiStoreGet || function(name, fallback){
    try{
      var current = window.localStorage.getItem(productKey(name));
      if(current !== null) return current;
      var legacy = window.localStorage.getItem(legacyKey(name));
      return legacy !== null ? legacy : (fallback || '');
    }catch(_){
      return fallback || '';
    }
  };
  window.__taijiStoreSet = window.__taijiStoreSet || function(name, value){
    try{ window.localStorage.setItem(productKey(name), value); }catch(_){}
  };
  window.__taijiStoreRemove = window.__taijiStoreRemove || function(name){
    try{
      window.localStorage.removeItem(productKey(name));
      window.localStorage.removeItem(legacyKey(name));
    }catch(_){}
  };

  try{ migrateLegacyStorage(window.localStorage); }catch(_){}
  try{ migrateLegacyStorage(window.sessionStorage); }catch(_){}
  try{ patchStoragePrototype(); }catch(_){}
})();
