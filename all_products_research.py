"""Strict Ferpotravina exact-label researcher for all retailer products."""
import json,re,sys,unicodedata
from hermes_tools import web_search
from dairy_research import fetch_ferpotravina, normalize

def tokens(s):
 s=normalize(s); s=re.sub(r'\b\d+[,.]?\d*\s*(g|kg|ml|l|ks)\b',' ',s)
 return {x for x in re.findall(r'[a-z0-9]+',s) if len(x)>=4 and x not in {'produkt','produkty','akce','premium','excellent','natural','bio','fresh','food','style','original','classic','klasik','special','speciál','choice','quality','good','great','ks'}}

def brand(label):
 # Prefer proper-name tokens and known multiword brands; source matching still requires token evidence.
 n=normalize(label)
 known=['hollandia','pilos','albert','tesco','billa','lidl','emco','emma','bonavita','nestle','olma','madeta','danone','alpro','allnature','topnatur','caribbean style','garden gourmet','nature promise','president','galbani','milka','opavia','be raw','bombus','4slim','vitana','hamé','kinder','dr oetker','valsoia','zott','kunin','ehrmann','activia','corny','merci','becherovka','aperol','campari','martini','mionetto','bohemia','kofola','coca cola','pepsi','mattoni','pilsner','gambrinus','staropramen']
 for x in sorted(known,key=len,reverse=True):
  if normalize(x) in n:return x
 return None

def search_one(label):
 b=brand(label); core=re.sub(r'\b\d+[,.]?\d*\s*(g|kg|ml|l|ks)\b',' ',normalize(label));
 q=f'site:ferpotravina.cz "{b}" {core}' if b else f'site:ferpotravina.cz "{core}"'
 out={'product_name':label,'source_url':None,'source_product':None,'producer':b,'values':{},'status':'not_found','match_reason':'no strict Ferpotravina match'}
 try: results=web_search(query=q,limit=6).get('data',{}).get('web',[])
 except Exception as e: out['status']='error';out['match_reason']=str(e);return out
 lt=tokens(label)
 for item in results:
  url=item.get('url','')
  if 'ferpotravina.cz' not in url:continue
  try: sp,prod,vals=fetch_ferpotravina(url)
  except Exception:continue
  if not sp or not vals:continue
  st=tokens(sp+' '+url)
  shared={x for x in lt if x in st or any(len(x)>5 and (x.startswith(y[:5]) or y.startswith(x[:5])) for y in st)}
  # Require several distinctive tokens; generic category words alone never qualify.
  distinctive={x for x in lt if x not in {'mleko','jogurt','syr','maslo','chleb','napoj','vino','pivo','caj','kava','ryze','mouka','cukr','smetana','vejce','vejce'}}
  if b and normalize(b) not in normalize(sp+' '+(prod or '')+' '+url):continue
  if distinctive and len(shared & distinctive)<max(1,min(2,len(distinctive))):continue
  if not distinctive and len(shared)<1:continue
  out.update(source_url=url,source_product=sp,producer=prod or b,values=vals,status='found',match_reason='strict brand and distinctive-token match')
  return out
 return out

def run(labels,path):
 out=[]
 for i,l in enumerate(labels,1):
  r=search_one(l);out.append(r);print(f'[{i}/{len(labels)}] {r["status"]} {l}',file=sys.stderr,flush=True)
 json.dump(out,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=2);return out
