<template>
 <ElDivider>接口采集</ElDivider>
 <ArtForm v-model="model" :items="connection" :span="12" label-position="top" :show-reset="false" :show-submit="false" />
 <template v-if="model.adapter==='wanxiao'">
  <ArtForm v-model="model" :items="campus" :span="12" label-position="top" :show-reset="false" :show-submit="false" />
  <ElSelect v-if="rooms.length" v-model="model.roomverify" placeholder="绑定房间" class="v1-full"><ElOption v-for="r in rooms" :key="r.roomverify" :label="r.label" :value="r.roomverify" /></ElSelect>
  <ElCollapse class="v1-gap"><ElCollapseItem title="导入请求参数" name="capture"><ElInput v-model="capture" type="textarea" :rows="4" placeholder="JSON / HAR / curl" /><ElButton class="v1-gap" :loading="parsing" @click="parse">解析</ElButton><ElSelect v-if="candidates.length>1" v-model="candidate" placeholder="选择账户" @change="fill"><ElOption v-for="(c,i) in candidates" :key="i" :value="i" :label="c.school_code+' · '+c.account" /></ElSelect><ElAlert v-if="message" :title="message" type="info" :closable="false" /></ElCollapseItem></ElCollapse>
 </template>
 <template v-else><ArtForm v-model="model" :items="mapping" :span="12" label-position="top" :show-reset="false" :show-submit="false" /></template>
 <ArtForm v-model="model" :items="policy" :span="12" label-position="top" :show-reset="false" :show-submit="false" />
</template>
<script setup>
import {ref,computed,watch} from 'vue';import {api} from './api';
const model=defineModel({required:true});defineProps({rooms:{default:()=>[]}});const capture=ref(''),message=ref(''),parsing=ref(false),candidates=ref([]),candidate=ref();
const f=(key,label,type='input',props={})=>({key,label,type,props});
const connection=computed(()=>[f('adapter','协议','select',{options:[{value:'generic',label:'通用 HTTP API'},{value:'wanxiao',label:'完美校园'}]}),f('interval','查询间隔 / 秒','number',{min:model.value.adapter==='wanxiao'?3600:60,max:86400}),{...f('url','请求地址','input',{placeholder:'https://'}),span:24},f('method','请求方式','select',{disabled:model.value.adapter==='wanxiao',options:[{value:'POST',label:'POST'},{value:'GET',label:'GET'}]}),f('encoding','编码','select',{disabled:model.value.adapter==='wanxiao',options:[{value:'json',label:'JSON'},{value:'form',label:'Form'}]}),{...f('headers_json','认证请求头','input',{type:'textarea',rows:3,spellcheck:false}),span:24}]);
const campus=[f('school_code','学校编号'),f('account','查询账户'),f('roomverify','房间标识'),f('business_type','业务类型'),f('max_queries','每日查询上限','number',{min:1,max:24}),f('window_start','查询开始','timepicker',{format:'HH:mm',valueFormat:'HH:mm'}),f('window_end','查询结束','timepicker',{format:'HH:mm',valueFormat:'HH:mm'})];
const mapping=[{...f('body_json','请求参数','input',{type:'textarea',rows:4,spellcheck:false}),span:24},f('mapping.balance_kwh','余额路径 / kWh'),f('mapping.cumulative_kwh','累计电量路径 / kWh'),f('mapping.power_w','功率路径 / W'),f('mapping.voltage','电压路径 / V'),f('success_path','成功状态路径'),f('success_value','成功状态值')];
const policy=computed(()=>[...((model.value.adapter==='wanxiao'||model.value.mapping?.balance_kwh)?[f('warning_kwh','预警电量 / kWh','number',{min:0}),f('critical_kwh','紧急电量 / kWh','number',{min:0}),f('reserve_kwh','保留电量 / kWh','number',{min:0}),f('forecast_notice_hours','提前预警 / 小时','number',{min:0,max:168})]:[]),f('enabled','自动查询','switch')]);
watch(()=>model.value.adapter,value=>{if(value==='wanxiao'){model.value.url||='https://xqh5.17wanxiao.com/smartWaterAndElectricityService/SWAEEncryptServlet';model.value.method='POST';model.value.encoding='form';model.value.interval=Math.max(3600,model.value.interval)}});
function fill(){const item=candidates.value[candidate.value];if(item)for(const key of ['school_code','account','roomverify','business_type','command'])if(item[key]!==undefined)model.value[key]=item[key]}
async function parse(){parsing.value=true;message.value='';try{candidates.value=(await api('device-capture',{source:capture.value})).candidates||[];if(candidates.value.length===1){candidate.value=0;fill()}message.value='已解析';capture.value=''}catch(e){message.value=e.message}finally{parsing.value=false}}
</script>
