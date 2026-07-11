/* Small, dependency-free focus lifecycle for the product's critical dialogs. */
const ManagedDialog=(()=>{
  const FOCUSABLE=[
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[contenteditable="true"]',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  function _resolveTarget(candidate,root){
    if(typeof candidate==='function') return candidate(root);
    if(typeof candidate==='string') return root.querySelector(candidate)||document.querySelector(candidate);
    return candidate||null;
  }

  function _canFocus(node){
    return !!(node&&typeof node.focus==='function'&&!node.disabled&&node.getAttribute('aria-hidden')!=='true');
  }

  function _focusable(root){
    return Array.from(root.querySelectorAll(FOCUSABLE)).filter(node=>{
      if(!_canFocus(node))return false;
      const style=window.getComputedStyle(node);
      return style.display!=='none'&&style.visibility!=='hidden'&&(node.offsetParent!==null||style.position==='fixed');
    });
  }

  function create(root,{initialFocus=null,returnFocus=null,closeOnBackdrop=false,onRequestClose=null,display=null}={}){
    if(!root)throw new Error('ManagedDialog requires a root element');
    const dialog=root.matches('[role="dialog"]')?root:root.querySelector('[role="dialog"]');
    if(!dialog)throw new Error('ManagedDialog requires role="dialog"');
    let opened=false;
    let previousFocus=null;

    function focusInitial(){
      let target=_resolveTarget(initialFocus,root);
      if(!_canFocus(target)||!root.contains(target))target=_focusable(dialog)[0]||dialog;
      if(!_canFocus(target))return;
      if(target===dialog&&!dialog.hasAttribute('tabindex'))dialog.setAttribute('tabindex','-1');
      target.focus();
    }

    function restorePreviousFocus(){
      let target=previousFocus;
      if(!_canFocus(target)||!target.isConnected||previousFocus===document.body||previousFocus===document.documentElement){
        target=_resolveTarget(returnFocus,document);
      }
      previousFocus=null;
      if(_canFocus(target)&&target.isConnected)target.focus();
    }

    function close({restoreFocus=true}={}){
      if(!opened)return;
      opened=false;
      document.removeEventListener('keydown',handleKeydown,true);
      root.removeEventListener('click',handleBackdrop);
      if(display)root.style.display='none';
      else root.hidden=true;
      if(restoreFocus)requestAnimationFrame(restorePreviousFocus);
      else previousFocus=null;
    }

    function requestClose(reason,event){
      if(typeof onRequestClose==='function')onRequestClose({reason,event,dialog:controller});
      else close();
    }

    function handleBackdrop(event){
      if(closeOnBackdrop&&event.target===root)requestClose('backdrop',event);
    }

    function handleKeydown(event){
      if(!opened)return;
      if(event.key==='Escape'){
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        requestClose('escape',event);
        return;
      }
      if(event.key==='Tab'){
        const items=_focusable(dialog);
        if(!items.length){
          event.preventDefault();
          if(!dialog.hasAttribute('tabindex'))dialog.setAttribute('tabindex','-1');
          dialog.focus();
          return;
        }
        const first=items[0];
        const last=items[items.length-1];
        const current=document.activeElement;
        if(event.shiftKey&&(current===first||!dialog.contains(current))){
          event.preventDefault();
          last.focus();
        }else if(!event.shiftKey&&(current===last||!dialog.contains(current))){
          event.preventDefault();
          first.focus();
        }
      }
    }

    function open(){
      if(opened)return;
      const activeElement=document.activeElement;
      previousFocus=root.contains(activeElement)?null:activeElement;
      opened=true;
      root.hidden=false;
      if(display)root.style.display=display;
      document.addEventListener('keydown',handleKeydown,true);
      if(closeOnBackdrop)root.addEventListener('click',handleBackdrop);
      requestAnimationFrame(focusInitial);
    }

    const controller={open,close,focusInitial,get isOpen(){return opened;}};
    return controller;
  }

  return {create};
})();
