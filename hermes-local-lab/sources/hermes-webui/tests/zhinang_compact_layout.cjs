'use strict';
// Called by the existing isolated server/Chromium harness; no real user state.
module.exports=async function({browser,base,OUT,evidence,check}){
  const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
  const files=['static/index.html','static/zhinang.css','static/zhinang.js','tests/zhinang_compact_layout.cjs','tests/zhinang_browser_e2e.cjs'];
  const hashes=()=>Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,'..',file))).digest('hex')]));
  evidence.compactSourceBefore=hashes();
  for(const [width,height] of [[1440,900],[1280,800],[1024,768],[768,1024],[390,844],[650,800],[720,450]]){
    const context=await browser.newContext({viewport:{width,height},locale:'zh-CN'});
    const page=await context.newPage();
    page.on('pageerror',error=>evidence.pageErrors.push(error.message));
    await page.route('**/*',route=>['127.0.0.1','localhost'].includes(new URL(route.request().url()).hostname)?route.continue():route.abort());
    await page.goto(base,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>S._bootReady===true);
    if(await page.locator('[data-taiji-panel="zhinang"]').isVisible())await page.locator('[data-taiji-panel="zhinang"]').click();
    else if(await page.locator('nav.rail [data-panel="zhinang"]').isVisible())await page.locator('nav.rail [data-panel="zhinang"]').click();
    else{await page.locator('#btnHamburger').click();await page.locator('.sidebar [data-panel="zhinang"]').click();}
    const ready=()=>page.locator('#zhinangStatus[data-state="ready"]').waitFor();
    await ready();
    await page.locator('[data-zhinang-view="all"]').click();await ready();
    const snapshot=async(label)=>{
      await page.locator('.zhinang-card .zhinang-role-image.is-loaded').first().waitFor();
      const file=path.join(OUT,`compact-${width}-${label}.png`);await page.screenshot({path:file,animations:'disabled'});evidence.screenshots.push({path:file});
      const m=await page.evaluate(()=>{
        const grid=document.querySelector('#zhinangGrid'),card=grid.querySelector('.zhinang-card'),pager=document.querySelector('#zhinangPagination');
        const r=card.getBoundingClientRect(),g=grid.getBoundingClientRect(),p=pager.getBoundingClientRect();
        return {scrollY,bodyScroll:document.body.scrollTop,headerTop:document.querySelector('.app-titlebar').getBoundingClientRect().top,scrollingNodes:[...document.querySelectorAll('*')].filter(n=>n.scrollTop).map(n=>({tag:n.tagName,id:n.id,cls:n.className,scroll:n.scrollTop})),columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,cardWidth:r.width,cardHeight:r.height,cardBottom:r.bottom,gridBottom:g.bottom,pagerTop:p.top,overflow:document.documentElement.scrollWidth-innerWidth,gridOverflow:grid.scrollWidth-grid.clientWidth};
      });
      evidence.checks.push({label:`${width}-${label}`,geometry:m});return m;
    };
    let m=await snapshot('expanded');
    if(width===1440)check(m.columns===3,'1440 expanded has three readable columns');
    if(width>=641&&width<=900){
      const nav=page.locator('nav.rail [data-panel="zhinang"]');
      await nav.hover();
      check(await nav.evaluate(n=>{const c=getComputedStyle(n,'::after');return c.position==='static'&&c.opacity==='1'&&c.content.includes('智囊库');}),`${width} scrollable rail keeps visible navigation labels`);
    }
    check(m.bodyScroll===0&&m.headerTop>=0,`${width} app header remains anchored`);
    if(width===720)check(m.cardBottom<=m.gridBottom,'short reflow viewport shows the complete first card');
    check(m.cardHeight<215,`${width} compact cards avoid tall whitespace`);
    check(m.overflow<=1&&m.gridOverflow<=1,`${width} no horizontal overflow`);
    check(m.gridBottom<=m.pagerTop+1,`${width} pagination never overlays grid`);
    check(await page.locator('#zhinangSearch').isVisible(),`${width} main search visible`);
    if(width>=1024){
      await page.locator('#taijiSecondaryToggle').click();
      await page.waitForFunction(()=>document.querySelector('.taiji-home-shell').dataset.secondaryCollapsed==='1');
      m=await snapshot('collapsed');
      if(width===1440)check(m.columns===4,'1440 collapsed has four readable columns');
      check(await page.locator('#zhinangSearch').isVisible(),`${width} search survives collapse`);
    }
    const search=page.locator('#zhinangSearch');
    await Promise.all([page.waitForResponse(r=>new URL(r.url()).searchParams.get('query')==='售前方案顾问'),search.fill('售前方案顾问')]);
    await ready();
    const role=page.locator('.zhinang-card').filter({has:page.getByRole('heading',{name:'售前方案顾问',exact:true})});
    check(await role.count()===1&&await page.locator('.zhinang-card').count()<24,`${width} search filters correct role`);
    const favorite=role.locator('.zhinang-favorite');
    if(await favorite.getAttribute('aria-pressed')==='true'){await favorite.click();await role.locator('.zhinang-favorite[aria-pressed="false"]').waitFor();}
    await favorite.focus();await page.keyboard.press('Enter');
    await role.locator('.zhinang-favorite[aria-pressed="true"]').waitFor();
    const detail=role.locator('[data-zhinang-open]');await detail.click();await page.locator('#zhinangDetailTitle').waitFor();
    check(await page.locator('.zhinang-primary').isVisible(),`${width} detail create entry visible`);
    if(width===1440){
      check(await page.locator('#zhinangDetail').getAttribute('data-mode')==='aside','wide collapsed detail uses aside');
      const detailMetrics=await snapshot('aside');
      check(detailMetrics.columns<4&&detailMetrics.overflow<=1&&detailMetrics.gridOverflow<=1,'aside reduces grid columns without overflow');
    }
    await page.keyboard.press('Escape');
    check(await detail.evaluate(node=>node===document.activeElement),`${width} detail restores focus`);
    await search.fill('no-such-role-compact');await page.locator('.zhinang-empty').waitFor();
    check(await page.locator('[data-zhinang-action="reset-filters"]').isVisible(),`${width} empty search offers recovery`);
    await page.locator('[data-zhinang-action="reset-filters"]').click();await ready();
    await page.locator('[data-zhinang-page="2"]').click();await ready();
    await page.waitForFunction(()=>document.querySelector('#zhinangPagination').textContent.includes('第 2'));
    check(await page.locator('.zhinang-card').count()===24,`${width} next page renders 24 roles`);
    if(width===390||width===650||width===720||width===768){
      if(await page.locator('#btnHamburger').isVisible())await page.locator('#btnHamburger').click();
      await page.locator('[data-zhinang-scope="favorites"]').click();await ready();
      check(await page.locator('.zhinang-card').count()===1,`${width} narrow favorites entry works`);
      await page.locator('[data-zhinang-scope="all"]').click();await ready();
      await page.locator('[data-zhinang-category="设计与体验"]').click();await ready();
      await page.waitForFunction(()=>[...document.querySelectorAll('.zhinang-card-heading>span')].every(n=>n.textContent==='设计与体验'));
      check(await page.locator('.zhinang-card').count()>0,`${width} narrow category entry works`);
      if(await page.locator('#mobileOverlay').isVisible())await page.locator('#mobileOverlay').click({position:{x:width-5,y:height/2}});
      check(await search.isVisible(),`${width} search returns after closing categories`);
    }
    if(width===1440){
      await page.locator('#taijiSecondaryToggle').click();
      await page.waitForFunction(()=>document.querySelector('.taiji-home-shell').dataset.secondaryCollapsed==='0');
      await page.locator('[data-zhinang-category="设计与体验"]').click();await ready();
      await page.waitForFunction(()=>[...document.querySelectorAll('.zhinang-card-heading>span')].every(n=>n.textContent==='设计与体验'));
      check(await page.locator('.zhinang-card').count()>0,'category filtering remains available');
      await page.locator('[data-zhinang-scope="favorites"]').click();await ready();
      check(await page.locator('.zhinang-card').count()===1,'favorites scope retains saved role');
      await page.locator('[data-zhinang-view="recent"]').click();await page.locator('.zhinang-empty').waitFor();
      check(await page.locator('[data-zhinang-action="browse-all"]').isVisible(),'recent empty state has browse recovery');
      await page.locator('[data-zhinang-action="browse-all"]').click();await ready();
      const apiPattern='**/api/zhinang/catalog**';
      await page.route(apiPattern,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'受控目录失败'})}));
      await page.locator('[data-zhinang-action="refresh"]').click();await page.locator('.zhinang-error').waitFor();
      check(await page.locator('.zhinang-error button').isVisible(),'catalog failure retains retry');
      await page.unroute(apiPattern);await page.locator('.zhinang-error button').click();await ready();
      check(await page.locator('.zhinang-card').count()===24,'catalog retry recovers');
      await page.locator('[data-taiji-panel="chat"]').click();
      await page.waitForFunction(()=>document.querySelector('.taiji-home-shell').dataset.activePanel==='chat');
      check(await page.locator('.taiji-brand-sidebar').evaluate(n=>n.getBoundingClientRect().width)>200,'other modules retain original sidebar width');
    }
    await context.close();
  }
  evidence.compactSourceAfter=hashes();
  check(JSON.stringify(evidence.compactSourceBefore)===JSON.stringify(evidence.compactSourceAfter),'source bytes unchanged throughout compact acceptance');
};
