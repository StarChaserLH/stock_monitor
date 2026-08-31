#!/bin/bash

# 从当前目录向上递归查找项目根目录（依据Pipfile判定pipenv项目）
find_project_root() {
	local current_dir="$PWD"
	while [[ "$current_dir" != "/" ]]; do
		if [[ -f "${current_dir}/Pipfile" ]]; then
			echo "$current_dir"
			return 0
		fi
		current_dir=$(dirname "$current_dir")
	done
	echo ""
	return 1
}
# 获取项目路径
PROJECT_ROOT=$(find_project_root)
# 判断是否找到有效项目目录
if [[ -z "$PROJECT_ROOT" ]]; then
	echo "错误：未找到包含Pipfile的pipenv项目目录，请在项目内执行脚本"
	exit 1
fi
echo "检测到项目目录：${PROJECT_ROOT}"
# 切换www-data执行依赖安装
runuser -u www-data -- env HOME=/var/www/.wwwdata-home bash -c "
cd ${PROJECT_ROOT}
pipenv run pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pipenv run pip install -r requirements.txt
"
# 执行结果反馈
if [ $? -eq 0 ]; then
	echo "依赖安装执行完成"
else
	echo "依赖安装过程出现异常"
	exit 1
fi
