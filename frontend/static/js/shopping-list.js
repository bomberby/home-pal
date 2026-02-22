
  // Shopping List Functions
  function addShoppingItem() {
      const input = document.getElementById('shopping-input');
      const list = document.getElementById('shopping-list');
      
      if (input.value.trim()) {
          fetch('/shopping-list-items', {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json'
              },
              body: JSON.stringify({
                  item_name: input.value.trim(),
                  quantity: 1,
                  purchased: false
              })
          })
          .then(response => response.json())
          .then(data => {
              const li = document.createElement('li');
              li.className = 'shopping-item';
              li.dataset.id = data.id;
              const span = document.createElement('span');
              span.className = 'item';
              span.textContent = data.item_name;
              const btn = document.createElement('button');
              btn.className = 'shopping-button';
              btn.textContent = 'x';
              btn.addEventListener('click', () => deleteShoppingItem(btn));
              li.appendChild(span);
              li.appendChild(btn);
              list.appendChild(li);
          })
          .catch(error => console.error('Error:', error));
          
          input.value = '';
      }
  }

  function deleteShoppingItem(button) {
      const li = button.parentElement;
      li.remove();
      fetch(`/shopping-list-items/${li.dataset.id}`, {
          method: 'DELETE'
      })
      .catch(error => console.error('Error:', error));
  }
  document.addEventListener('DOMContentLoaded', function() {
      fetch('/shopping-list-items')
          .then(response => response.json())
          .then(data => {
              data.forEach(item => {
                  const li = document.createElement('li');
                  li.className = 'shopping-item';
                  li.dataset.id = item.id;
                  const span = document.createElement('span');
                  span.className = 'item';
                  span.textContent = item.item_name;
                  const btn = document.createElement('button');
                  btn.className = 'shopping-button';
                  btn.textContent = 'x';
                  btn.addEventListener('click', () => deleteShoppingItem(btn));
                  li.appendChild(span);
                  li.appendChild(btn);
                  document.getElementById('shopping-list').appendChild(li);
              });
          })
          .catch(error => console.error('Error:', error));
      
      // Add event listener for the Enter key
      document.getElementById('shopping-input').addEventListener('keypress', function(e) {
          if (e.key === 'Enter') {
              addShoppingItem();
          }
      });
  });