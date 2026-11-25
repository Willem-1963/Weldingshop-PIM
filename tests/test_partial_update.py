from copy import deepcopy
from app.shopify.partial_update import changed_fields
from app.shopify import sync


def match():
    return {'product':{'id':'P1','title':'Aansluitsok','descriptionHtml':'<p>Rijk</p>','status':'ACTIVE'},
            'variant':{'id':'V1','price':'11.95','barcode':'123','inventoryItem':{'unitCost':{'amount':'8.00'}}}}


def test_invoice_changes_cost_only_and_does_not_require_html_or_tags():
    source={'sku':'50601','source_title':'HD SOK factuur','sale_price':None,'cost_price':8.45,
            '_raw_data':{'invoice_import':{'source':'purchase_invoice'}}}
    product,variant=changed_fields(source,match(),None,'')
    assert product=={'id':'P1'}
    assert variant=={'id':'V1','inventoryItem':{'cost':'8.45'}}


def test_only_actual_differences_and_no_empty_values():
    source={'sku':'1','source_title':'Aansluitsok','cost_price':8,'ean':'123'}
    assert changed_fields(source,match(),11.95,'<p>Rijk</p>')==({'id':'P1'},{'id':'V1'})
    source.update(source_title='',ean='',cost_price=None)
    assert changed_fields(source,match(),0,'')==({'id':'P1'},{'id':'V1'})
    source.update(ai_title='Nieuwe titel')
    assert changed_fields(source,match(),12.95,'')[0]=={'id':'P1','title':'Nieuwe titel'}


def test_partial_bulk_preserves_other_variants_status_channels_and_tree(monkeypatch):
    calls=[]
    monkeypatch.setattr(sync,'_run_rows',lambda client,rows,mutation,*args: calls.append((rows,mutation)))
    source={'sku':'50601','cost_price':8.45,'_raw_data':{'invoice_import':{ 'source':'purchase_invoice'}}}
    original=match();before=deepcopy(original)
    refreshed=deepcopy(original)
    refreshed['variant']['inventoryItem']['unitCost']['amount']='8.45'
    monkeypatch.setattr(sync,'get_supplier',lambda slug:{'name':'Gasflessen.nl'})
    monkeypatch.setattr(sync,'_shopify_products',lambda *args,**kwargs:{'50601':refreshed})
    report=sync._sync_existing_partial(None,'gas',[source],{'50601':original},None)
    assert original==before
    assert calls[0][0]==[]
    assert calls[1][0]==[{'productId':'P1','variants':[{'id':'V1','inventoryItem':{'cost':'8.45'}}]}]
    assert report['updated']==1
    assert report['details'][0]['fields']==['inventoryItem']


def test_sync_existing_partial_bypasses_quality_gate_for_existing_only(monkeypatch):
    source={'sku':'50601','cost_price':8.45,'source_title':'HD SOK','raw_data_json':'{"invoice_import":{"source":"purchase_invoice"}}'}
    supplier={'name':'Gasflessen.nl','source_type':'csv_upload','sync_new_product_policy':'existing_only','request_options':{'existing_partial_update':True}}
    monkeypatch.setattr(sync,'get_shopify_settings',lambda:{'enabled':True})
    monkeypatch.setattr(sync,'get_supplier',lambda slug:supplier)
    monkeypatch.setattr(sync,'_selected_inventory_location',lambda *a:'L1')
    monkeypatch.setattr(sync.ShopifyClient,'from_settings',lambda:object())
    monkeypatch.setattr(sync,'_source_products',lambda slug:[source])
    monkeypatch.setattr(sync,'_load_family_variant_options',lambda slug:{})
    monkeypatch.setattr(sync,'_load_certilas_family_pim_context',lambda slug:{})
    monkeypatch.setattr(sync,'_missing_products',lambda slug:[])
    monkeypatch.setattr(sync,'_draft_excluded_collections',lambda *a:set())
    monkeypatch.setattr(sync,'_collection_inventory_policy_skus',lambda *a:(set(),set(),set()))
    monkeypatch.setattr(sync,'_load_sync_hashes',lambda *a:{})
    monkeypatch.setattr(sync,'_select_changed_products',lambda *a:([source],{'50601':'hash'},[]))
    monkeypatch.setattr(sync,'_shopify_products',lambda *a,**kw:{'50601':match()})
    saved=[]
    monkeypatch.setattr(sync,'_save_sync_hashes',lambda *a:saved.append(a))
    monkeypatch.setattr(sync,'_apply_collection_inventory_policies',lambda *a:(_ for _ in ()).throw(AssertionError('Geen voorraadbeleid wijzigen')))
    monkeypatch.setattr(sync,'_sync_existing_partial',lambda *a:{'updated':1,'details':[]})
    result=sync.sync_all_products('gasflessen-nl-72784841')
    assert result['ok']
    assert result['uploaded_or_updated']==1
    assert saved[0][1]=={'50601':'hash'}


def test_new_products_still_require_complete_content():
    assert not sync._complete_for_new_product({'sku':'NEW','source_title':'Titel','cost_price':5,'sale_price':10})
