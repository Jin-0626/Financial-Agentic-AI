"""Independent, bounded clients for five public financial-data APIs."""
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
import requests

ROOTS={"bnm":"https://api.bnm.gov.my/public", "fred":"https://api.stlouisfed.org/fred",
       "worldbank":"https://api.worldbank.org/v2", "treasury":"https://api.fiscaldata.treasury.gov/services/api/fiscal_service", "sec":"https://data.sec.gov"}
CURRENCIES="AED AUD BND CAD CHF CNY EGP EUR GBP HKD IDR INR JPY KHR KRW MMK NOK NPR NZD PHP PKR SAR SDR THB TWD USD VND".split()
COMMANDS={"bnm":{"available","currencies","currency","fx","major","asean","opr","interest","base_rate","gold"},
 "fred":{"series","search","categories","category_series","releases"},
 "worldbank":{"indicators","economic_snapshot","gdp_per_capita","commodity_prices"},
 "treasury":{"debt","interest_rates","exchange_rates","avg_rates","record_debt"},
 "sec":{"cik_map","symbol_map","company_filings","available_form_types","company_facts"}}


def token(value, pattern=r"[A-Za-z0-9._-]{1,128}"):
    if not isinstance(value,str) or not re.fullmatch(pattern,value): raise ValueError("Invalid provider identifier")
    return value


def limit(value, default=50):
    result=int(value) if value is not None else default
    if not 1<=result<=500: raise ValueError("Limit must be 1..500")
    return result


def dates(args):
    if len(args)>2: raise ValueError("At most two date bounds are allowed")
    values=[date.fromisoformat(value).isoformat() for value in args]
    if len(values)==2 and values[0]>values[1]: raise ValueError("Date bounds are reversed")
    return values


def fetch(url, params=None, provider=None):
    headers={"Accept":"application/vnd.BNM.API.v1+json"} if provider=="bnm" else {}
    if provider=="sec":
        identity=os.getenv("SEC_USER_AGENT","").strip()
        if not identity or not re.search(r"[^\s@]+@[^\s@]+",identity): raise ValueError("Set SEC_USER_AGENT with an application name and contact email")
        headers["User-Agent"]=identity
    with requests.Session() as session:
        with session.get(url,params=params,headers=headers,timeout=(5,20),stream=True) as response:
            response.raise_for_status(); chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>8*1024*1024: raise ValueError("Provider response exceeds size limit")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))


def bnm(command,args):
    if command=="available": return {"success":True,"commands":sorted(COMMANDS["bnm"]),"all_currencies":CURRENCIES,"data":{"all_currencies":CURRENCIES,"major_currencies":"USD EUR GBP JPY AUD CHF CAD CNY".split()},"source":"Bank Negara Malaysia","url":ROOTS["bnm"]}
    if command=="currencies": return {"success":True,"currencies":CURRENCIES,"count":len(CURRENCIES)}
    route={"currency":"exchange-rate","fx":"exchange-rate","major":"exchange-rate","asean":"exchange-rate","opr":"opr","interest":"interest-rate","base_rate":"base-rate","gold":"kijang-emas"}[command]
    session=args[1] if command=="currency" and len(args)>1 else args[0] if command in {"fx","major","asean"} and args else "1130"
    if session not in {"0900","1130","1200","1700"}: raise ValueError("Unsupported BNM rate session")
    if command=="currency":
        if not args: raise ValueError("Currency code is required")
        route+="/"+token(args[0].upper(),r"[A-Z]{3}")
    url=ROOTS["bnm"]+"/"+route
    raw=fetch(url,{"session":session} if route.startswith("exchange-rate") else None,"bnm")
    rows=raw.get("data",[])
    if not route.startswith("exchange-rate"): return {"success":True,"data":rows,"meta":raw.get("meta",{}),"source":"Bank Negara Malaysia","url":url}
    rows=rows if isinstance(rows,list) else [rows];full=[];flat={}
    selected=set("USD EUR GBP JPY AUD CHF CAD CNY".split()) if command=="major" else set("SGD THB IDR PHP VND BND MMK KHR LAK".split()) if command=="asean" else None
    for row in rows:
        currency=row.get("currency_code") or row.get("currency")
        if selected and currency not in selected: continue
        unit=Decimal(str(row.get("unit",1)))
        if unit<=0: raise ValueError("Provider returned invalid currency units")
        rate=row.get("rate") or {}; record={"currency":currency,"date":rate.get("date") or row.get("date"),"unit":str(unit)}
        for key in ("buying","selling","middle"):
            value=rate.get(key);record[key]=None if value is None else str(Decimal(str(value))/unit)
        full.append(record);flat[currency]=record["middle"] if record["middle"] is not None else record["buying"]
    return {"success":True,"session":session,"date":full[0]["date"] if full else "","data":flat,"full_data":full,"count":len(full),"source":"Bank Negara Malaysia","url":url,"meta":raw.get("meta",{}),"note":"MYR per one foreign currency unit"}


def fred(command,args):
    key=os.getenv("FRED_API_KEY")
    if not key: raise ValueError("FRED_API_KEY is required")
    base={"api_key":key,"file_type":"json"}
    def request(endpoint,parameters): return fetch(ROOTS["fred"]+"/"+endpoint,{**base,**parameters},"fred")
    if command=="series":
        ids=[token(x) for x in args if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",x)]
        bounds=dates([x for x in args if re.fullmatch(r"\d{4}-\d{2}-\d{2}",x)])
        if not 1<=len(ids)<=10: raise ValueError("Supply 1..10 series identifiers")
        output={}
        for identifier in ids:
            parameters={"series_id":identifier,"limit":500}
            if bounds: parameters["observation_start"]=bounds[0]
            if len(bounds)>1: parameters["observation_end"]=bounds[1]
            raw=request("series/observations",parameters)
            metadata=request("series",{"series_id":identifier}).get("seriess",[{}])[0]
            observations=raw.get("observations",[])
            output[identifier]={"series_id":identifier,"title":metadata.get("title"),"units":metadata.get("units"),"frequency":metadata.get("frequency"),"observations":observations,"count":len(observations),"last_updated":metadata.get("last_updated"),"source":"FRED","metadata":{"truncated":int(raw.get("count",len(observations)))>len(observations)}}
        return output[ids[0]] if len(ids)==1 else output
    if command=="search":
        if not args or len(args[0])>200: raise ValueError("Search text of 1..200 characters is required")
        raw=request("series/search",{"search_text":args[0],"limit":limit(args[1] if len(args)>1 else None,10)})
        return {"search_text":args[0],"count":raw.get("count",0),"series":raw.get("seriess",[])}
    routes={"categories":("category", "category_id", "categories"),"category_series":("category/series","category_id","seriess"),"releases":("release/dates","release_id","release_dates")}
    route,field,result_field=routes[command]
    identifier=int(args[0]) if args else 0
    if identifier<0: raise ValueError("Provider identifier cannot be negative")
    raw=request(route,{field:identifier,"limit":limit(args[1] if len(args)>1 else None)})
    return {"data":raw.get(result_field,[]),**raw}


def worldbank(command,args):
    country="ALL" if command=="commodity_prices" else token((args[0] if args else "MYS").upper(),r"[A-Z,;]{2,128}").replace(",",";")
    def observations(indicator,window=None):
        parameters={"format":"json","per_page":500}
        if window is not None: parameters["date"]=window
        url=f"{ROOTS['worldbank']}/country/{country}/indicator/{token(indicator)}"
        raw=fetch(url,parameters,"worldbank")
        if not isinstance(raw,list) or len(raw)!=2: raise ValueError("World Bank did not return indicator observations")
        meta,rows=raw
        return {"data":rows or [],"metadata":{**meta,"source":"World Bank","source_url":url,"truncated":int(meta.get("pages",1))>1},"error":None}
    if command=="economic_snapshot":
        codes={"gdp":"NY.GDP.MKTP.CD","gdp_per_capita":"NY.GDP.PCAP.CD","growth":"NY.GDP.MKTP.KD.ZG","inflation":"FP.CPI.TOTL.ZG","unemployment":"SL.UEM.TOTL.ZS"}
        return {"country":country,"source":"World Bank","indicators":{name:observations(code) for name,code in codes.items()}}
    if command=="indicators":
        code=args[1] if len(args)>1 else "NY.GDP.PCAP.CD"
        window=args[2] if len(args)>2 else None
        if window and not re.fullmatch(r"\d{4}(?::\d{4})?",window): raise ValueError("Indicator window must be YYYY or YYYY:YYYY")
        return observations(code,window)
    years=limit(args[1] if len(args)>1 else None,20 if command=="gdp_per_capita" else 10)
    if command=="commodity_prices":
        country="ALL";code=args[0] if args else "CRUDE_PETRO";code=token(code)
        return observations(code,f"{date.today().year-years+1}:{date.today().year}")
    return observations("NY.GDP.PCAP.CD",f"{date.today().year-years+1}:{date.today().year}")


def treasury(command,args):
    routes={"debt":"v2/accounting/od/debt_to_penny","record_debt":"v2/accounting/od/debt_to_penny","interest_rates":"v2/accounting/od/avg_interest_rates","avg_rates":"v2/accounting/od/avg_interest_rates","exchange_rates":"v1/accounting/od/rates_of_exchange"}
    filters=[]
    if command=="interest_rates":
        security=args[0] if args else "Treasury Bills"
        if not re.fullmatch(r"[A-Za-z -]{1,60}",security): raise ValueError("Invalid security type")
        filters.append("security_desc:eq:"+security);args=args[1:]
    bounds=dates(args)
    if bounds: filters.append("record_date:gte:"+bounds[0])
    if len(bounds)>1: filters.append("record_date:lte:"+bounds[1])
    params={"page[size]":500,"sort":"-record_date"}
    if filters: params["filter"]=",".join(filters)
    return fetch(ROOTS["treasury"]+"/"+routes[command],params,"treasury")


def sec(command,args):
    if command=="available_form_types": return {"form_types":["10-K","10-Q","8-K","20-F","6-K","S-1","DEF 14A","4","13F-HR"]}
    if command in {"cik_map","symbol_map"}:
        raw=fetch("https://www.sec.gov/files/company_tickers.json",provider="sec")
        query=token(args[0]).upper() if args else None
        records=[row for row in raw.values() if not query or (str(row.get("ticker","")).upper()==query if command=="cik_map" else str(row.get("cik_str",0))==str(int(query)))]
        return {"data":records,"count":len(records)}
    if command=="company_facts":
        if not args or not re.fullmatch(r"\d{1,10}",args[0]): raise ValueError("A numeric CIK is required")
        return fetch(f"{ROOTS['sec']}/api/xbrl/companyfacts/CIK{int(args[0]):010d}.json",provider="sec")
    symbol=args[0] if args else None;cik=args[1] if len(args)>1 else None
    if not cik:
        mapping=sec("cik_map",[symbol] if symbol else [])
        if len(mapping["data"])!=1: raise ValueError("An exact symbol or CIK is required")
        cik=str(mapping["data"][0]["cik_str"])
    if not re.fullmatch(r"\d{1,10}",cik): raise ValueError("CIK must be numeric")
    form=token(args[2]) if len(args)>2 else None
    bounds=dates(args[3:5]);maximum=limit(args[5] if len(args)>5 else None)
    raw=fetch(f"{ROOTS['sec']}/submissions/CIK{int(cik):010d}.json",provider="sec")
    recent=raw.get("filings",{}).get("recent",{}); filings=[]
    for i, filed in enumerate(recent.get("filingDate",[])):
        if (form and recent.get("form",[])[i]!=form) or (bounds and filed<bounds[0]) or (len(bounds)>1 and filed>bounds[1]): continue
        record={key:value[i] for key,value in recent.items() if isinstance(value,list) and i<len(value)}
        record["url"]=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{record['accessionNumber'].replace('-','')}/{record['primaryDocument']}"
        filings.append(record)
    return {"cik":f"{int(cik):010d}","name":raw.get("name"),"data":filings[:maximum],"count":min(len(filings),maximum),"limitations":["Recent submissions only; older filing archive pages are not fetched"]}

DISPATCH={"bnm":bnm,"fred":fred,"worldbank":worldbank,"treasury":treasury,"sec":sec}


def run(provider,args=None):
    args=list(sys.argv[1:] if args is None else args)
    try:
        if not args or args[0] not in COMMANDS[provider]: raise ValueError("Unknown or unsupported curated command")
        if len(args)>10 or any(len(x)>500 for x in args): raise ValueError("Provider argument limits exceeded")
        if provider=="sec" and not re.search(r"[^\s@]+@[^\s@]+",os.getenv("SEC_USER_AGENT","")):
            raise ValueError("Set SEC_USER_AGENT with an application name and contact email")
        result=DISPATCH[provider](args[0],args[1:])
    except requests.RequestException as error:
        result={"status":"error","error":{"category":"provider","message":"Provider HTTP request failed","status_code":error.response.status_code if error.response is not None else None}}
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        result={"status":"error","error":{"category":"validation","message":str(error)[:300]}}
    print(json.dumps(result,allow_nan=False))
